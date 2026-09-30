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
from archive.quant_research_v1.pipeline.validate_residual_stability_v41 import _fit_fold
from src.services.nepse_quant_research import NEPSE_INDEX_NAME
from archive.quant_research_v1.services.quant_execution_aware import OUTER_FOLDS, expanding_nested_folds
from archive.quant_research_v1.services.quant_historical_context import attach_exact_regime_context
from archive.quant_research_v1.services.quant_portfolio_simulator import (
    SIM_BLOCK_LENGTH,
    SIM_MAX_HOLDING_SESSIONS,
    SIM_MAX_POSITIONS,
    SIM_ROUND_TRIP_COST_PERCENT,
    block_bootstrap_incremental_returns,
    simulate_rebalance_portfolio,
)
from archive.quant_research_v1.services.quant_predictable_investability import prepare_predictable_investable_rows


def _signals(selection: dict[str, Any]) -> dict[Any, list[str]]:
    grouped: dict[Any, list[tuple[float, str]]] = defaultdict(list)
    for candidate in selection.get("selected", []):
        row = candidate["row"]
        score = float(candidate.get("final_score", row.get("baseline_score", 0.0)))
        grouped[row["date"]].append((score, str(row["symbol"])))
    return {
        trading_date: [symbol for _, symbol in sorted(items, reverse=True)]
        for trading_date, items in grouped.items()
    }


def _merge_signals(target: dict[Any, list[str]], source: dict[Any, list[str]]) -> None:
    for trading_date, symbols in source.items():
        target[trading_date] = list(symbols)


def _benchmark_metrics(dates: list[Any], closes: list[float]) -> dict[str, Any]:
    if len(dates) < 2 or len(closes) != len(dates):
        return {"sessions": len(dates), "status": "insufficient"}
    raw_returns = [
        float(current) / float(previous) - 1.0
        for previous, current in zip(closes[:-1], closes[1:])
        if float(previous) > 0
    ]
    # Strategy accounting contains an explicit first decision-session observation
    # (which can include entry costs). Align the benchmark to those same dates with
    # a zero first-session return rather than silently dropping the strategy's
    # initial execution cost from paired block-bootstrap comparisons.
    aligned_returns = [0.0, *raw_returns]
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
    daily_std = pstdev(aligned_returns) if len(aligned_returns) >= 2 else 0.0
    return {
        "status": "simulated",
        "sessions": len(dates),
        "total_return_percent": (ending - 1.0) * 100.0,
        "approx_cagr_percent": (ending ** (1.0 / years) - 1.0) * 100.0,
        "annualized_volatility_percent": daily_std * math.sqrt(252.0) * 100.0 if daily_std > 0 else None,
        "sharpe_like": mean(aligned_returns) / daily_std * math.sqrt(252.0) if daily_std > 0 else None,
        "max_drawdown_percent": worst * 100.0,
        "daily_returns": [
            {"date": trading_date, "return": value}
            for trading_date, value in zip(dates, aligned_returns)
        ],
    }


def validate_v41_portfolio(*, limit: int = DEFAULT_SYMBOL_LIMIT, folds: int = OUTER_FOLDS) -> dict[str, Any]:
    pooled, universe = _build_pooled_rows(limit=limit)
    attach_exact_regime_context(pooled)
    investable, investability = prepare_predictable_investable_rows(pooled)
    fold_specs = expanding_nested_folds(investable, folds=folds)

    v41_signals: dict[Any, list[str]] = {}
    baseline_signals: dict[Any, list[str]] = {}
    valid_folds = 0
    selected_rows: list[dict[str, Any]] = []
    baseline_rows: list[dict[str, Any]] = []
    for fold in fold_specs:
        result = _fit_fold(fold)
        if result is None:
            continue
        valid_folds += 1
        selection = result.get("_selection", {"selected": [], "days": []})
        baseline = result.get("_baseline", {"selected": [], "days": []})
        _merge_signals(v41_signals, _signals(selection))
        _merge_signals(baseline_signals, _signals(baseline))
        selected_rows.extend(candidate["row"] for candidate in selection.get("selected", []))
        baseline_rows.extend(candidate["row"] for candidate in baseline.get("selected", []))

    if not v41_signals or not baseline_signals:
        return {
            "status": "no_oos_signals",
            "valid_folds": valid_folds,
            "universe": universe,
            "investability": investability,
        }

    start_date = min(set(v41_signals) | set(baseline_signals))
    end_candidates = [
        row.get("label_end_date") or row["date"]
        for row in [*selected_rows, *baseline_rows]
    ]
    end_date = max(end_candidates) if end_candidates else max(set(v41_signals) | set(baseline_signals))
    symbols = sorted({symbol for values in [*v41_signals.values(), *baseline_signals.values()] for symbol in values})

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

    v41 = simulate_rebalance_portfolio(
        market_dates=market_dates,
        signals_by_date=v41_signals,
        price_history=dict(price_history),
        max_positions=SIM_MAX_POSITIONS,
        max_holding_sessions=SIM_MAX_HOLDING_SESSIONS,
        round_trip_cost_percent=SIM_ROUND_TRIP_COST_PERCENT,
    )
    baseline = simulate_rebalance_portfolio(
        market_dates=market_dates,
        signals_by_date=baseline_signals,
        price_history=dict(price_history),
        max_positions=SIM_MAX_POSITIONS,
        max_holding_sessions=SIM_MAX_HOLDING_SESSIONS,
        round_trip_cost_percent=SIM_ROUND_TRIP_COST_PERCENT,
    )
    nepse = _benchmark_metrics(market_dates, market_closes)
    incremental = block_bootstrap_incremental_returns(
        v41.get("daily_returns", []),
        baseline.get("daily_returns", []),
        block_length=SIM_BLOCK_LENGTH,
    )
    v41_vs_nepse = block_bootstrap_incremental_returns(
        v41.get("daily_returns", []),
        nepse.get("daily_returns", []),
        block_length=SIM_BLOCK_LENGTH,
    )

    compact_keys = (
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
        "mean_positions",
        "mean_completed_holding_sessions",
        "median_completed_holding_sessions",
    )
    compact_v41 = {key: v41.get(key) for key in compact_keys}
    compact_baseline = {key: baseline.get(key) for key in compact_keys}
    compact_nepse = {
        key: nepse.get(key)
        for key in (
            "sessions",
            "total_return_percent",
            "approx_cagr_percent",
            "annualized_volatility_percent",
            "sharpe_like",
            "max_drawdown_percent",
        )
    }

    return {
        "status": "ready",
        "valid_outer_folds": valid_folds,
        "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "execution_policy": {
            "max_positions": SIM_MAX_POSITIONS,
            "weighting": "equal weight",
            "rebalance": "every available frozen OOS V4.1 decision date",
            "maximum_holding_sessions": SIM_MAX_HOLDING_SESSIONS,
            "round_trip_cost_percent": SIM_ROUND_TRIP_COST_PERCENT,
            "price_basis": "adjusted close when available, otherwise close",
            "leverage": False,
            "shorting": False,
            "terminal_liquidation_cost_charged": True,
        },
        "historical_cadence_limitation": {
            "feature_step": HISTORICAL_STEP,
            "daily_mark_to_market": True,
            "daily_historical_signal_claim": False,
            "note": (
                "The frozen historical V4.1 OOS matrix was sampled every five stock observations. The simulator marks "
                "capital every NEPSE session and enforces daily holding limits, but only rebalances when a genuine "
                "historical OOS V4.1 decision exists. The new forward ledger captures every future eligible session."
            ),
        },
        "summary": {
            "v41": compact_v41,
            "matched_momentum_baseline": compact_baseline,
            "nepse": compact_nepse,
            "v41_minus_baseline_cagr_percent": (
                float(v41.get("approx_cagr_percent")) - float(baseline.get("approx_cagr_percent"))
                if v41.get("approx_cagr_percent") is not None and baseline.get("approx_cagr_percent") is not None
                else None
            ),
            "v41_minus_nepse_cagr_percent": (
                float(v41.get("approx_cagr_percent")) - float(nepse.get("approx_cagr_percent"))
                if v41.get("approx_cagr_percent") is not None and nepse.get("approx_cagr_percent") is not None
                else None
            ),
        },
        "block_bootstrap": {
            "v41_vs_matched_baseline": incremental,
            "v41_vs_nepse": v41_vs_nepse,
        },
        "research_interpretation": {
            "promotion_gate": False,
            "purpose": "execution realism diagnostic for the already frozen V4.1 historical challenger",
            "forward_shadow_still_required": True,
        },
        "investability": investability,
        "universe": universe,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Realistic portfolio simulation for frozen V4.1 OOS selections")
    parser.add_argument("--limit", type=int, default=DEFAULT_SYMBOL_LIMIT)
    parser.add_argument("--folds", type=int, default=OUTER_FOLDS)
    args = parser.parse_args()
    print(json.dumps(validate_v41_portfolio(limit=args.limit, folds=args.folds), indent=2, default=str))


if __name__ == "__main__":
    main()
