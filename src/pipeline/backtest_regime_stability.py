from __future__ import annotations

import itertools
import logging
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import (
    ConfluenceConfidence,
    SignalConfidence,
    SignalConfidenceTier,
    SignalRegimeStability,
)
from src.pipeline.backtest_signals import backtest_multiple_signals_in_range
from src.pipeline.run_signal_backtests import build_signal_conditions

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HISTORY_START = date(2021, 7, 25)
HISTORY_END = date(2026, 7, 23)
MIDPOINT = date(2024, 1, 23)
FORWARD_DAYS = [10]

FIRST_HALF = ("first_half", HISTORY_START, MIDPOINT)
SECOND_HALF = ("second_half", MIDPOINT, HISTORY_END)

BASELINE_LABEL = "baseline_unconditional"


def _always_true(row, close, prev_row, prev_close) -> bool:
    return True


def _load_high_confidence_signal_names() -> list[str]:
    with get_session() as session:
        rows = session.execute(
            select(SignalConfidence.signal_name)
            .where(SignalConfidence.tier == SignalConfidenceTier.HIGH_CONFIDENCE)
            .order_by(SignalConfidence.signal_name)
        ).all()
    return [r.signal_name for r in rows]


def _load_top_confluence_pairs(limit: int = 3) -> list[tuple[str, str]]:
    with get_session() as session:
        rows = session.execute(
            select(ConfluenceConfidence.signal_a, ConfluenceConfidence.signal_b)
            .where(ConfluenceConfidence.tier == "consistent_high_confidence")
            .order_by(ConfluenceConfidence.avg_win_rate_minus_baseline.desc())
            .limit(limit)
        ).all()
    return [(r.signal_a, r.signal_b) for r in rows]


def _build_condition_set() -> tuple[dict[str, Any], list[str], list[tuple[str, str]]]:
    all_conditions = build_signal_conditions()
    solo_names = _load_high_confidence_signal_names()
    confluence_pairs = _load_top_confluence_pairs()

    conditions = {name: all_conditions[name] for name in solo_names}
    for signal_a, signal_b in confluence_pairs:
        cond_a = all_conditions[signal_a]
        cond_b = all_conditions[signal_b]

        def combined(row, close, prev_row, prev_close, cond_a=cond_a, cond_b=cond_b):
            return cond_a(row, close, prev_row, prev_close) and cond_b(row, close, prev_row, prev_close)

        conditions[f"{signal_a} & {signal_b}"] = combined

    conditions[BASELINE_LABEL] = _always_true
    return conditions, solo_names, confluence_pairs


def run_regime_stability_check() -> list[dict[str, Any]]:
    conditions, solo_names, confluence_pairs = _build_condition_set()
    pair_names = [f"{a} & {b}" for a, b in confluence_pairs]
    logger.info(
        "Running regime stability check for %d solo signals and %d confluence pairs",
        len(solo_names),
        len(pair_names),
    )

    rows_by_period = {}
    for period, start_date, end_date in (FIRST_HALF, SECOND_HALF):
        dedup_conditions = {k: v for k, v in conditions.items() if k != BASELINE_LABEL}
        results = backtest_multiple_signals_in_range(
            dedup_conditions, FORWARD_DAYS, start_date, end_date, dedup_episodes=True
        )
        baseline_result = backtest_multiple_signals_in_range(
            {BASELINE_LABEL: _always_true}, FORWARD_DAYS, start_date, end_date, dedup_episodes=False
        )
        results[BASELINE_LABEL] = baseline_result[BASELINE_LABEL]
        rows_by_period[period] = results

    baseline_win_rate = {
        period: rows_by_period[period][BASELINE_LABEL]["forward_days"][FORWARD_DAYS[0]]["win_rate"]
        for period, _, _ in (FIRST_HALF, SECOND_HALF)
    }

    all_names = solo_names + pair_names + [BASELINE_LABEL]
    records = []
    for signal_name in all_names:
        for period, _, _ in (FIRST_HALF, SECOND_HALF):
            stats = rows_by_period[period][signal_name]["forward_days"][FORWARD_DAYS[0]]
            win_rate = stats["win_rate"]
            baseline = baseline_win_rate[period]
            win_rate_minus_baseline = win_rate - baseline if win_rate is not None and baseline is not None else None
            records.append(
                {
                    "signal_name": signal_name,
                    "period": period,
                    "sample_size": stats["sample_size"],
                    "win_rate_minus_baseline": win_rate_minus_baseline,
                }
            )

    stored = _upsert_regime_stability(records)
    logger.info("Stored %d signal_regime_stability rows", stored)
    return records


def _upsert_regime_stability(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(SignalRegimeStability).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["signal_name", "period"],
        set_={
            "sample_size": stmt.excluded.sample_size,
            "win_rate_minus_baseline": stmt.excluded.win_rate_minus_baseline,
        },
    )
    with get_session() as session:
        session.execute(stmt)
    return len(rows)


if __name__ == "__main__":
    run_regime_stability_check()
