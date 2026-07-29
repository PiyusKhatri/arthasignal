from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import LiquidityStratifiedBacktestResult, SymbolLiquidityTier
from src.pipeline.backtest_signals import (
    DEFAULT_FORWARD_DAYS,
    _evaluate_signal,
    _load_daily_signal_entries,
    _load_price_series,
)
from src.pipeline.compute_baseline import BASELINE_SIGNAL_NAME, _always_true
from src.pipeline.run_signal_backtests import build_signal_conditions

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HIGH_CONFIDENCE_SIGNALS = [
    "rsi_14 < 30 (oversold)",
    "close < bollinger_lower",
    "close > bollinger_upper",
    "shooting_star",
    "rsi_14 > 70 (overbought)",
    "marubozu_bearish",
    "marubozu_bullish",
    "stochastic_k > 80",
    "doji",
]

LIQUIDITY_TIERS = ("high_liquidity", "medium_liquidity", "low_liquidity")


def _load_tier_symbols() -> dict[str, set[str]]:
    with get_session() as session:
        rows = session.execute(select(SymbolLiquidityTier)).scalars().all()
        by_tier: dict[str, set[str]] = {tier: set() for tier in LIQUIDITY_TIERS}
        for row in rows:
            by_tier[row.liquidity_tier].add(row.symbol)
        return by_tier


def _upsert_results(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(LiquidityStratifiedBacktestResult).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["signal_name", "liquidity_tier", "forward_days"],
        set_={
            "sample_size": stmt.excluded.sample_size,
            "win_rate": stmt.excluded.win_rate,
            "win_rate_minus_baseline": stmt.excluded.win_rate_minus_baseline,
            "computed_at": stmt.excluded.computed_at,
        },
    )
    with get_session() as session:
        session.execute(stmt)
    return len(rows)


def run_liquidity_stratified_backtest(forward_days: list[int] = DEFAULT_FORWARD_DAYS) -> dict[str, Any]:
    tier_symbols = _load_tier_symbols()
    for tier, symbols in tier_symbols.items():
        logger.info("%s: %d symbols", tier, len(symbols))

    all_conditions = build_signal_conditions()
    signal_conditions = {name: all_conditions[name] for name in HIGH_CONFIDENCE_SIGNALS}

    logger.info("Loading full price series and signal entries once")
    price_series = _load_price_series()
    all_entries = _load_daily_signal_entries()

    computed_at = datetime.utcnow()
    rows = []
    per_tier_results: dict[str, dict[str, Any]] = {}

    for tier in LIQUIDITY_TIERS:
        symbols = tier_symbols[tier]
        tier_entries = [(row, close) for row, close in all_entries if row.symbol in symbols]
        logger.info("%s: %d signal_entries in scope", tier, len(tier_entries))

        baseline_result = _evaluate_signal(
            BASELINE_SIGNAL_NAME, _always_true, forward_days, tier_entries, price_series, dedup_episodes=False
        )
        baseline_win_rate_by_horizon = {
            n: stats["win_rate"] for n, stats in baseline_result["forward_days"].items()
        }

        tier_results = {BASELINE_SIGNAL_NAME: baseline_result}
        for signal_name, condition_fn in signal_conditions.items():
            tier_results[signal_name] = _evaluate_signal(
                signal_name, condition_fn, forward_days, tier_entries, price_series, dedup_episodes=True
            )

        per_tier_results[tier] = tier_results

        for signal_name, result in tier_results.items():
            for n in forward_days:
                stats = result["forward_days"][n]
                baseline_win_rate = baseline_win_rate_by_horizon.get(n)
                win_rate_minus_baseline = None
                if stats["win_rate"] is not None and baseline_win_rate is not None:
                    win_rate_minus_baseline = stats["win_rate"] - baseline_win_rate

                rows.append(
                    {
                        "signal_name": signal_name,
                        "liquidity_tier": tier,
                        "forward_days": n,
                        "sample_size": stats["sample_size"],
                        "win_rate": stats["win_rate"],
                        "win_rate_minus_baseline": win_rate_minus_baseline,
                        "computed_at": computed_at,
                    }
                )

    stored = _upsert_results(rows)
    logger.info("Stored %d liquidity_stratified_backtest_results rows", stored)

    return {"rows_stored": stored, "results": per_tier_results}


if __name__ == "__main__":
    run_liquidity_stratified_backtest()
