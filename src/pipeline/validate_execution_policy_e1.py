from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from statistics import mean, pstdev
from typing import Any

from sqlalchemy import select

from src.database.connection import get_session
from src.database.models import DailyPrice, MarketIndex
from src.pipeline.train_quant_models import DEFAULT_SYMBOL_LIMIT, HISTORICAL_STEP, _build_pooled_rows
from src.pipeline.validate_residual_stability_v41 import (
    _calibrate_affine_target,
    _calibrate_monotonic_residual,
    _calibrate_residual_probability,
)
from src.services.nepse_quant_research import NEPSE_INDEX_NAME
from src.services.quant_execution_aware import OUTER_FOLDS, expanding_nested_folds
from src.services.quant_execution_policy_e1 import (
    E1_MAX_HOLDING_SESSIONS,
    E1_MAX_POSITIONS,
    E1_POLICY_VERSION,
    E1_REPLACEMENT_SCORE_MARGIN,
    E1_ROUND_TRIP_COST_PERCENT,
    group_predictions_by_date,
    simulate_execution_policy_e1,
)
from src.services.quant_historical_context import attach_exact_regime_context
from src.services.quant_portfolio_simulator import (
    SIM_BLOCK_LENGTH,
    block_bootstrap_incremental_returns,
    simulate_rebalance_portfolio,
)
from src.services.quant_predictable_investability import prepare_predictable_investable_rows
from src.services.quant_residual_alpha import (
    attach_residual_targets,
    build_baseline_candidate_pool,
    fit_baseline_expectation,
    fit_v4_classifier,
    fit_v4_regressor,
    matched_baseline_selection,
    selection_counts,
)
from src.services.quant_residual_stability import build_v41_predictions, select_v41_setups

E1_MAX_ANNUALIZED_TURNOVER_X = 30.0
E1_MIN_MEAN_HOLDING_SESSIONS = 5.0
E1_MIN_TURNOVER_REDUCTION_VS_REACTIVE = 0.60


def _fit_fold_predictions(fold: dict[str, Any]) -> dict[str, Any] | None:
    """Recreate the frozen V4.1 fold predictions without changing V4.1 itself."""
    train_candidates = build_baseline_candidate_pool(fold["train"])
    calibration_candidates = build_baseline_candidate_pool(fold["calibration"])
    test_candidates = build_baseline_candidate_pool(fold["test"])
    if min(len(train_candidates), len(calibration_candidates), len(test_candidates)) < 100:
        return None

    baseline_model = fit_baseline_expectation(train_candidates)
    train = attach_residual_targets(train_candidates, baseline_model)
    calibration = attach_residual_targets(calibration_candidates, baseline_model)
    test = attach_residual_targets(test_candidates, baseline_model)

    residual_model = fit_v4_regressor(train, target_key="residual_alpha_percent", clip_low=-25.0, clip_high=25.0)
    residual_classifier = fit_v4_classifier(train)
    mae_model = fit_v4_regressor(train, target_key="mae_magnitude_percent", clip_low=0.0, clip_high=25.0)
    mfe_model = fit_v4_regressor(train, target_key="mfe_percent", clip_low=0.0, clip_high=35.0)
    if any(model is None for model in (residual_model, residual_classifier, mae_model, mfe_model)):
        return None

    residual_predictions, _ = _calibrate_monotonic_residual(residual_model, calibration, test)
    residual_probabilities, _ = _calibrate_residual_probability(residual_classifier, calibration, test)
    mae_predictions, _ = _calibrate_affine_target(
        mae_model,
        calibration,
        test,
        target_key="mae_magnitude_percent",
        floor=0.0,
        ceiling=25.0,
    )
    mfe_predictions, _ = _calibrate_affine_target(
        mfe_model,
        calibration,
        test,
        target_key="mfe_percent",
        floor=0.0,
        ceiling=35.0,
    )
    if not (
        len(test)
        == len(residual_predictions)
        == len(residual_probabilities)
        == len(mae_predictions)
        == len(mfe_predictions)
    ):
        return None

    predictions = build_v41_predictions(
        test,
        residual_predictions,
        residual_probabilities,
        mae_predictions,
        mfe_predictions,
    )
    selection = select_v41_setups(predictions)
    baseline = matched_baseline_selection(test, selection_counts(selection))
    return {
        "fold": int(fold["fold"]),
        "predictions": predictions,
        "selection": selection,
        "matched_baseline": baseline,
    }


def _signals(selection: dict[str, Any], *, score_key: str) -> dict[Any, list[str]]:
    grouped: dict[Any, list[tuple[float, str]]] = defaultdict(list)
    for candidate in selection.get("selected", []):
        row = candidate["row"]
        score = float(candidate.get(score_key, row.get("baseline_score", 0.0)))
        grouped[row["date"]].append((score, str(row["symbol"])))
    return {
        trading_date: [symbol for _, symbol in sorted(items, reverse=True)]
        for trading_date, items in grouped.items()
    }


def _benchmark_metrics(dates: list[Any], closes: list[float]) -> dict[str, Any]:
    if len(dates) < 2 or len(closes) != len(dates):
        return {"status": "insufficient", "sessions": len(dates)}
    raw_returns = [
        float(current) / float(previous) - 1.0
        for previous, current in zip(closes[:-1], closes[1:])
        if float(previous) > 0
    ]
    returns = [0.0, *raw_returns]
    wealth = [1.0]
    for value in raw_returns:
        wealth.append(wealth[-1] * (1.0 + value))
    peak = wealth[0]
    worst = 0.0
    for value in wealth:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1.0)
    years = max(1.0 / 252.0, len(dates) / 252.0)
    ending = wealth[-1]
    daily_std = pstdev(returns) if len(returns) >= 2 else 0.0
    return {
        "status": "simulated",
        "sessions": len(dates),
        "total_return_percent": (ending - 1.0) * 100.0,
        "approx_cagr_percent": (ending ** (1.0 / years) - 1.0) * 100.0,
        "annualized_volatility_percent": daily_std * math.sqrt(252.0) * 100.0 if daily_std > 0 else None,
        "sharpe_like": mean(returns) / daily_std * math.sqrt(252.0) if daily_std > 0 else None,
        "max_drawdown_percent": worst * 100.0,
        "daily_returns": [
            {"date": trading_date, "return": value}
            for trading_date, value in zip(dates, returns)
        ],
    }


def _compact(result: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "sessions",
        "decision_dates",
        "total_return_percent",
        "approx_cagr_percent",
        "annualized_volatility_percent",
        "sharpe_like",
        "max_drawdown_percent",
        "total_transaction_cost_percent_of_initial_capital",
        "cumulative_turnover_x_initial_capital",
        "annualized_turnover_x",
        "buy_transactions",
        "sell_transactions",
        "replacements",
        "blocked_buy_attempts",
        "blocked_sell_attempts",
        "sampled_absence_holds",
        "mean_positions",
        "mean_completed_holding_sessions",
        "median_completed_holding_sessions",
        "exit_reasons",
    )
    return {key: result.get(key) for key in keys if key in result}


def validate_execution_policy_e1(*, limit: int = DEFAULT_SYMBOL_LIMIT, folds: int = OUTER_FOLDS) -> dict[str, Any]:
    pooled, universe = _build_pooled_rows(limit=limit)
    attach_exact_regime_context(pooled)
    investable, investability = prepare_predictable_investable_rows(pooled)
    fold_specs = expanding_nested_folds(investable, folds=folds)

    all_predictions: list[dict[str, Any]] = []
    reactive_v41_signals: dict[Any, list[str]] = {}
    reactive_baseline_signals: dict[Any, list[str]] = {}
    observed_symbols_by_date: dict[Any, set[str]] = defaultdict(set)
    valid_folds = 0
    all_rows: list[dict[str, Any]] = []
    for fold in fold_specs:
        result = _fit_fold_predictions(fold)
        if result is None:
            continue
        valid_folds += 1
        predictions = result["predictions"]
        all_predictions.extend(predictions)
        all_rows.extend(candidate["row"] for candidate in predictions)
        for row in fold["test"]:
            observed_symbols_by_date[row["date"]].add(str(row["symbol"]))
        reactive_v41_signals.update(_signals(result["selection"], score_key="final_score"))
        reactive_baseline_signals.update(_signals(result["matched_baseline"], score_key="baseline_score"))

    if not all_predictions:
        return {
            "status": "no_oos_predictions",
            "policy_version": E1_POLICY_VERSION,
            "valid_outer_folds": valid_folds,
            "investability": investability,
            "universe": universe,
        }

    predictions_by_date = group_predictions_by_date(all_predictions)
    start_date = min(set(predictions_by_date) | set(observed_symbols_by_date))
    end_date = max((row.get("label_end_date") or row["date"]) for row in all_rows)
    symbols = sorted({candidate["row"]["symbol"] for candidate in all_predictions})

    with get_session() as session:
        market_rows = session.execute(
            select(MarketIndex.date, MarketIndex.close)
            .where(
                MarketIndex.index_name == NEPSE_INDEX_NAME,
                MarketIndex.date >= start_date,
                MarketIndex.date <= end_date,
            )
            .order_by(MarketIndex.date)
        ).all()
        price_rows = session.execute(
            select(DailyPrice.symbol, DailyPrice.date, DailyPrice.adjusted_close, DailyPrice.close)
            .where(
                DailyPrice.symbol.in_(symbols),
                DailyPrice.date >= start_date,
                DailyPrice.date <= end_date,
            )
            .order_by(DailyPrice.symbol, DailyPrice.date)
        ).all()

    market_dates = [row.date for row in market_rows if row.close is not None]
    market_closes = [float(row.close) for row in market_rows if row.close is not None]
    price_history: dict[str, dict[Any, float]] = defaultdict(dict)
    for row in price_rows:
        value = row.adjusted_close if row.adjusted_close is not None else row.close
        if value is not None and float(value) > 0:
            price_history[str(row.symbol)][row.date] = float(value)

    e1_v41 = simulate_execution_policy_e1(
        market_dates=market_dates,
        predictions_by_date=predictions_by_date,
        observed_symbols_by_date=dict(observed_symbols_by_date),
        price_history=dict(price_history),
        mode="v41",
    )
    e1_baseline = simulate_execution_policy_e1(
        market_dates=market_dates,
        predictions_by_date=predictions_by_date,
        observed_symbols_by_date=dict(observed_symbols_by_date),
        price_history=dict(price_history),
        mode="baseline",
    )
    reactive_v41 = simulate_rebalance_portfolio(
        market_dates=market_dates,
        signals_by_date=reactive_v41_signals,
        price_history=dict(price_history),
        max_positions=E1_MAX_POSITIONS,
        max_holding_sessions=E1_MAX_HOLDING_SESSIONS,
        round_trip_cost_percent=E1_ROUND_TRIP_COST_PERCENT,
    )
    reactive_baseline = simulate_rebalance_portfolio(
        market_dates=market_dates,
        signals_by_date=reactive_baseline_signals,
        price_history=dict(price_history),
        max_positions=E1_MAX_POSITIONS,
        max_holding_sessions=E1_MAX_HOLDING_SESSIONS,
        round_trip_cost_percent=E1_ROUND_TRIP_COST_PERCENT,
    )
    nepse = _benchmark_metrics(market_dates, market_closes)

    e1_vs_baseline = block_bootstrap_incremental_returns(
        e1_v41.get("daily_returns", []),
        e1_baseline.get("daily_returns", []),
        block_length=SIM_BLOCK_LENGTH,
    )
    e1_vs_reactive = block_bootstrap_incremental_returns(
        e1_v41.get("daily_returns", []),
        reactive_v41.get("daily_returns", []),
        block_length=SIM_BLOCK_LENGTH,
    )
    e1_vs_nepse = block_bootstrap_incremental_returns(
        e1_v41.get("daily_returns", []),
        nepse.get("daily_returns", []),
        block_length=SIM_BLOCK_LENGTH,
    )

    reactive_turnover = reactive_v41.get("annualized_turnover_x")
    e1_turnover = e1_v41.get("annualized_turnover_x")
    turnover_reduction = (
        1.0 - float(e1_turnover) / float(reactive_turnover)
        if e1_turnover is not None and reactive_turnover not in (None, 0.0)
        else None
    )
    reactive_cost = reactive_v41.get("total_transaction_cost_percent_of_initial_capital")
    e1_cost = e1_v41.get("total_transaction_cost_percent_of_initial_capital")
    cost_reduction = (
        1.0 - float(e1_cost) / float(reactive_cost)
        if e1_cost is not None and reactive_cost not in (None, 0.0)
        else None
    )
    bootstrap_low = e1_vs_baseline.get("annualized_incremental_return_95", {}).get("low")
    checks = {
        "enough_valid_outer_folds": valid_folds >= 3,
        "e1_v41_cagr_positive": float(e1_v41.get("approx_cagr_percent") or -999.0) > 0.0,
        "e1_v41_cagr_better_than_reactive_v41": (
            e1_v41.get("approx_cagr_percent") is not None
            and reactive_v41.get("approx_cagr_percent") is not None
            and float(e1_v41["approx_cagr_percent"]) > float(reactive_v41["approx_cagr_percent"])
        ),
        "annualized_turnover_at_most_30x": e1_turnover is not None and float(e1_turnover) <= E1_MAX_ANNUALIZED_TURNOVER_X,
        "turnover_reduction_vs_reactive_at_least_60pct": (
            turnover_reduction is not None and turnover_reduction >= E1_MIN_TURNOVER_REDUCTION_VS_REACTIVE
        ),
        "transaction_cost_lower_than_reactive": (
            e1_cost is not None and reactive_cost is not None and float(e1_cost) < float(reactive_cost)
        ),
        "mean_holding_at_least_5_sessions": (
            e1_v41.get("mean_completed_holding_sessions") is not None
            and float(e1_v41["mean_completed_holding_sessions"]) >= E1_MIN_MEAN_HOLDING_SESSIONS
        ),
        "e1_v41_cagr_better_than_e1_baseline": (
            e1_v41.get("approx_cagr_percent") is not None
            and e1_baseline.get("approx_cagr_percent") is not None
            and float(e1_v41["approx_cagr_percent"]) > float(e1_baseline["approx_cagr_percent"])
        ),
        "e1_vs_baseline_block_bootstrap_lower_bound_positive": (
            bootstrap_low is not None and float(bootstrap_low) > 0.0
        ),
        "max_drawdown_not_worse_than_reactive_v41": (
            e1_v41.get("max_drawdown_percent") is not None
            and reactive_v41.get("max_drawdown_percent") is not None
            and float(e1_v41["max_drawdown_percent"]) >= float(reactive_v41["max_drawdown_percent"])
        ),
    }
    passed = all(checks.values())

    return {
        "status": "ready",
        "policy_version": E1_POLICY_VERSION,
        "development_verdict": "candidate_for_forward_execution_shadow" if passed else "continue_execution_research",
        "valid_outer_folds": valid_folds,
        "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "execution_policy": {
            "predictive_model_changed": False,
            "max_positions": E1_MAX_POSITIONS,
            "max_holding_sessions": E1_MAX_HOLDING_SESSIONS,
            "round_trip_cost_percent": E1_ROUND_TRIP_COST_PERCENT,
            "replacement_score_margin": E1_REPLACEMENT_SCORE_MARGIN,
            "routine_rebalance_surviving_positions": False,
            "held_name_survives_ordinary_rank_drift": True,
            "v41_exit_triggers": ["fresh_ineligibility", "reject", "20_session_expiry", "material_replacement"],
            "baseline_uses_same_execution_mechanics": True,
            "baseline_same_breadth_guaranteed": False,
            "historical_unsampled_absence_causes_exit": False,
        },
        "summary": {
            "e1_v41": _compact(e1_v41),
            "e1_momentum_baseline": _compact(e1_baseline),
            "reactive_v41": _compact(reactive_v41),
            "reactive_matched_baseline": _compact(reactive_baseline),
            "nepse": {
                key: nepse.get(key)
                for key in (
                    "sessions",
                    "total_return_percent",
                    "approx_cagr_percent",
                    "annualized_volatility_percent",
                    "sharpe_like",
                    "max_drawdown_percent",
                )
            },
            "turnover_reduction_vs_reactive_v41": turnover_reduction,
            "transaction_cost_reduction_vs_reactive_v41": cost_reduction,
            "e1_minus_reactive_v41_cagr_percent": (
                float(e1_v41["approx_cagr_percent"]) - float(reactive_v41["approx_cagr_percent"])
                if e1_v41.get("approx_cagr_percent") is not None and reactive_v41.get("approx_cagr_percent") is not None
                else None
            ),
            "e1_minus_e1_baseline_cagr_percent": (
                float(e1_v41["approx_cagr_percent"]) - float(e1_baseline["approx_cagr_percent"])
                if e1_v41.get("approx_cagr_percent") is not None and e1_baseline.get("approx_cagr_percent") is not None
                else None
            ),
        },
        "block_bootstrap": {
            "e1_v41_vs_e1_momentum_baseline": e1_vs_baseline,
            "e1_v41_vs_reactive_v41": e1_vs_reactive,
            "e1_v41_vs_nepse": e1_vs_nepse,
        },
        "research_gate": {"status": "pass" if passed else "review", "checks": checks},
        "historical_cadence_limitation": {
            "feature_step": HISTORICAL_STEP,
            "daily_mark_to_market": True,
            "daily_historical_signal_claim": False,
            "sampled_absence_is_not_treated_as_ineligibility": True,
            "note": (
                "E1 is evaluated on the frozen V4.1 OOS matrix sampled every five stock observations. Holdings are "
                "marked and expiry-tested every NEPSE session. A held stock exits for ineligibility only when that stock "
                "has a fresh OOS observation and fails the candidate filter; an unsampled absence carries the last valid "
                "score. The forward V4.1 ledger will eventually provide complete daily observations without this approximation."
            ),
        },
        "investability": investability,
        "universe": universe,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate sticky Execution Policy E1 on frozen V4.1 OOS predictions")
    parser.add_argument("--limit", type=int, default=DEFAULT_SYMBOL_LIMIT)
    parser.add_argument("--folds", type=int, default=OUTER_FOLDS)
    args = parser.parse_args()
    print(json.dumps(validate_execution_policy_e1(limit=args.limit, folds=args.folds), indent=2, default=str))


if __name__ == "__main__":
    main()
