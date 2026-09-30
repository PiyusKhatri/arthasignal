from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from statistics import mean
from typing import Any

from sqlalchemy import select

from src.database.connection import get_session
from src.database.models import DailyPrice, MarketIndex
from src.pipeline.train_quant_models import DEFAULT_SYMBOL_LIMIT, HISTORICAL_STEP, _build_pooled_rows
from archive.quant_research_v1.pipeline.validate_execution_policy_e1 import _fit_fold_predictions
from src.services.nepse_quant_research import NEPSE_INDEX_NAME
from archive.quant_research_v1.services.quant_execution_aware import OUTER_FOLDS, expanding_nested_folds
from archive.quant_research_v1.services.quant_execution_diagnostics import (
    DIAGNOSTIC_POLICY_VERSION,
    contiguous_block_diagnostics,
    holding_diagnostics,
    reconstruct_completed_trades,
    rolling_performance_diagnostics,
    trade_breakdowns,
    turnover_source_diagnostics,
)
from archive.quant_research_v1.services.quant_execution_policy_e1 import (
    E1_MAX_HOLDING_SESSIONS,
    E1_MAX_POSITIONS,
    E1_POLICY_VERSION,
    E1_REPLACEMENT_SCORE_MARGIN,
    E1_ROUND_TRIP_COST_PERCENT,
    group_predictions_by_date,
    simulate_execution_policy_e1,
)
from archive.quant_research_v1.services.quant_historical_context import attach_exact_regime_context
from archive.quant_research_v1.services.quant_predictable_investability import prepare_predictable_investable_rows


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


def _period_context(
    predictions: list[dict[str, Any]],
    *,
    start: Any,
    end: Any,
) -> dict[str, Any]:
    rows = [
        candidate
        for candidate in predictions
        if start <= candidate["row"]["date"] <= end
    ]
    if not rows:
        return {
            "prediction_rows": 0,
            "market_regimes": {},
            "override_actions": {},
            "sectors": {},
        }
    market = Counter(str(candidate.get("market_regime") or candidate["row"].get("v4_market_regime") or "unknown") for candidate in rows)
    actions = Counter(str(candidate.get("override_action") or "hold") for candidate in rows)
    sectors = Counter(str(candidate["row"].get("sector") or "unknown") for candidate in rows)
    return {
        "prediction_rows": len(rows),
        "market_regimes": dict(market.most_common()),
        "override_actions": dict(actions.most_common()),
        "top_sectors": dict(sectors.most_common(8)),
    }


def _annotate_periods(
    section: dict[str, Any],
    predictions: list[dict[str, Any]],
    *,
    keys: tuple[str, ...],
) -> None:
    for key in keys:
        rows = section.get(key, [])
        for row in rows:
            row["context"] = _period_context(
                predictions,
                start=row["start"],
                end=row["end"],
            )


def _dominant_turnover_source(turnover: dict[str, Any]) -> dict[str, Any] | None:
    reasons = turnover.get("by_exit_reason", {})
    if not reasons:
        return None
    reason, metrics = max(
        reasons.items(),
        key=lambda item: float(item[1].get("sell_notional_x_initial_capital") or 0.0),
    )
    return {"reason": reason, **metrics}


def diagnose_execution_policy_e1(
    *,
    limit: int = DEFAULT_SYMBOL_LIMIT,
    folds: int = OUTER_FOLDS,
) -> dict[str, Any]:
    pooled, universe = _build_pooled_rows(limit=limit)
    attach_exact_regime_context(pooled)
    investable, investability = prepare_predictable_investable_rows(pooled)
    fold_specs = expanding_nested_folds(investable, folds=folds)

    all_predictions: list[dict[str, Any]] = []
    observed_symbols_by_date: dict[Any, set[str]] = defaultdict(set)
    all_rows: list[dict[str, Any]] = []
    fold_periods: list[dict[str, Any]] = []
    valid_folds = 0
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
        fold_periods.append(
            {
                "fold": int(fold["fold"]),
                "test_start": min(row["date"] for row in fold["test"]),
                "test_end": max(row["date"] for row in fold["test"]),
            }
        )

    if not all_predictions:
        return {
            "status": "no_oos_predictions",
            "diagnostic_policy_version": DIAGNOSTIC_POLICY_VERSION,
            "execution_policy_version": E1_POLICY_VERSION,
            "valid_outer_folds": valid_folds,
            "investability": investability,
            "universe": universe,
        }

    predictions_by_date = group_predictions_by_date(all_predictions)
    start_date = min(set(predictions_by_date) | set(observed_symbols_by_date))
    end_date = max((row.get("label_end_date") or row["date"]) for row in all_rows)
    symbols = sorted({str(candidate["row"]["symbol"]) for candidate in all_predictions})

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

    completed = reconstruct_completed_trades(
        e1_v41.get("trade_log", []),
        market_dates=market_dates,
        price_history=dict(price_history),
        predictions=all_predictions,
    )
    turnover = turnover_source_diagnostics(e1_v41.get("trade_log", []))
    holding = holding_diagnostics(completed)
    rolling = rolling_performance_diagnostics(
        e1_v41.get("daily_returns", []),
        e1_baseline.get("daily_returns", []),
    )
    blocks = contiguous_block_diagnostics(
        e1_v41.get("daily_returns", []),
        e1_baseline.get("daily_returns", []),
    )
    breakdowns = trade_breakdowns(completed)

    for window_data in rolling.values():
        _annotate_periods(window_data, all_predictions, keys=("worst_5", "best_5"))
    _annotate_periods(blocks, all_predictions, keys=("worst_10", "best_10"))

    baseline_daily = {
        row["date"]: float(row["return"])
        for row in e1_baseline.get("daily_returns", [])
    }
    v41_daily = {
        row["date"]: float(row["return"])
        for row in e1_v41.get("daily_returns", [])
    }
    fold_diagnostics: list[dict[str, Any]] = []
    for fold in fold_periods:
        dates = sorted(
            trading_date
            for trading_date in set(v41_daily) & set(baseline_daily)
            if fold["test_start"] <= trading_date <= fold["test_end"]
        )
        if not dates:
            continue
        v41_compound = 1.0
        baseline_compound = 1.0
        for trading_date in dates:
            v41_compound *= 1.0 + v41_daily[trading_date]
            baseline_compound *= 1.0 + baseline_daily[trading_date]
        fold_diagnostics.append(
            {
                **fold,
                "sessions": len(dates),
                "v41_return_percent": (v41_compound - 1.0) * 100.0,
                "baseline_return_percent": (baseline_compound - 1.0) * 100.0,
                "incremental_return_percent": (v41_compound - baseline_compound) * 100.0,
                "context": _period_context(
                    all_predictions,
                    start=fold["test_start"],
                    end=fold["test_end"],
                ),
            }
        )

    replacement_trades = [row for row in completed if row.get("exit_reason") == "replacement"]
    replacement_summary = {
        "completed_replacements": len(replacement_trades),
        "mean_holding_sessions_before_replacement": (
            mean(int(row["holding_sessions"]) for row in replacement_trades)
            if replacement_trades else None
        ),
        "mean_gross_return_before_replacement_percent": (
            mean(
                float(row["gross_stock_return_percent"])
                for row in replacement_trades
                if row.get("gross_stock_return_percent") is not None
            )
            if any(row.get("gross_stock_return_percent") is not None for row in replacement_trades)
            else None
        ),
    }

    return {
        "status": "ready",
        "diagnostic_policy_version": DIAGNOSTIC_POLICY_VERSION,
        "execution_policy_version": E1_POLICY_VERSION,
        "diagnostics_only": True,
        "predictive_model_changed": False,
        "execution_policy_changed": False,
        "valid_outer_folds": valid_folds,
        "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "e1_frozen_assumptions": {
            "max_positions": E1_MAX_POSITIONS,
            "max_holding_sessions": E1_MAX_HOLDING_SESSIONS,
            "round_trip_cost_percent": E1_ROUND_TRIP_COST_PERCENT,
            "replacement_score_margin": E1_REPLACEMENT_SCORE_MARGIN,
        },
        "summary": {
            "e1_v41": _compact(e1_v41),
            "e1_momentum_baseline": _compact(e1_baseline),
            "dominant_turnover_source": _dominant_turnover_source(turnover),
            "completed_trades": len(completed),
            "negative_20_session_block_rate": (
                1.0 - float(blocks["positive_block_rate"])
                if blocks.get("positive_block_rate") is not None
                else None
            ),
        },
        "turnover_sources": turnover,
        "holding_periods": holding,
        "replacement_diagnostics": replacement_summary,
        "fold_diagnostics": fold_diagnostics,
        "rolling_incremental_performance": rolling,
        "contiguous_20_session_blocks": blocks,
        "trade_breakdowns": breakdowns,
        "interpretation_contract": {
            "purpose": "explain E1 v1 turnover and historical instability without tuning or changing E1",
            "historical_feature_step": HISTORICAL_STEP,
            "future_change_requires_new_execution_policy_version": True,
            "no_gate_or_parameter_is_changed_by_this command": True,
        },
        "investability": investability,
        "universe": universe,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose frozen E1 execution turnover and weak historical blocks")
    parser.add_argument("--limit", type=int, default=DEFAULT_SYMBOL_LIMIT)
    parser.add_argument("--folds", type=int, default=OUTER_FOLDS)
    args = parser.parse_args()
    print(json.dumps(diagnose_execution_policy_e1(limit=args.limit, folds=args.folds), indent=2, default=str))


if __name__ == "__main__":
    main()
