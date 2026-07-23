from __future__ import annotations

import itertools
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import BacktestResult, ConfluenceBacktestResult, SignalConfidence, SignalConfidenceTier
from src.pipeline.backtest_signals import DEFAULT_FORWARD_DAYS, backtest_multiple_signals
from src.pipeline.compute_baseline import BASELINE_SIGNAL_NAME
from src.pipeline.run_signal_backtests import build_signal_conditions

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LOW_SAMPLE_THRESHOLD = 500
PAIR_SEPARATOR = " & "


def _load_high_confidence_signal_names() -> list[str]:
    with get_session() as session:
        rows = session.execute(
            select(SignalConfidence.signal_name)
            .where(SignalConfidence.tier == SignalConfidenceTier.HIGH_CONFIDENCE)
            .order_by(SignalConfidence.signal_name)
        ).all()
    return [r.signal_name for r in rows]


def _load_baseline_win_rate_by_horizon() -> dict[int, Any]:
    with get_session() as session:
        rows = session.execute(
            select(BacktestResult.forward_days, BacktestResult.win_rate).where(
                BacktestResult.signal_name == BASELINE_SIGNAL_NAME
            )
        ).all()
    return {r.forward_days: r.win_rate for r in rows}


def _build_pair_conditions(signal_names: list[str], all_conditions: dict[str, Any]) -> dict[tuple[str, str], Any]:
    pair_conditions = {}
    for signal_a, signal_b in itertools.combinations(signal_names, 2):
        cond_a = all_conditions[signal_a]
        cond_b = all_conditions[signal_b]

        def combined(row, close, prev_row, prev_close, cond_a=cond_a, cond_b=cond_b):
            return cond_a(row, close, prev_row, prev_close) and cond_b(row, close, prev_row, prev_close)

        pair_conditions[(signal_a, signal_b)] = combined
    return pair_conditions


def run_confluence_backtest(forward_days: list[int] = DEFAULT_FORWARD_DAYS) -> dict[str, Any]:
    signal_names = _load_high_confidence_signal_names()
    logger.info("Running confluence backtest across %d high_confidence signals", len(signal_names))

    all_conditions = build_signal_conditions()
    pair_conditions = _build_pair_conditions(signal_names, all_conditions)
    logger.info("Testing %d pair combinations", len(pair_conditions))

    keyed_conditions = {
        f"{signal_a}{PAIR_SEPARATOR}{signal_b}": fn for (signal_a, signal_b), fn in pair_conditions.items()
    }
    results = backtest_multiple_signals(keyed_conditions, forward_days)

    baseline_win_rate_by_horizon = _load_baseline_win_rate_by_horizon()
    computed_at = datetime.utcnow()

    rows = []
    for (signal_a, signal_b) in pair_conditions:
        pair_key = f"{signal_a}{PAIR_SEPARATOR}{signal_b}"
        pair_result = results[pair_key]
        for n in forward_days:
            stats = pair_result["forward_days"][n]
            baseline_win_rate = baseline_win_rate_by_horizon.get(n)
            win_rate_minus_baseline = None
            if stats["win_rate"] is not None and baseline_win_rate is not None:
                win_rate_minus_baseline = stats["win_rate"] - baseline_win_rate

            rows.append(
                {
                    "signal_a": signal_a,
                    "signal_b": signal_b,
                    "forward_days": n,
                    "sample_size": stats["sample_size"],
                    "win_rate": stats["win_rate"],
                    "mean_return": stats["mean_forward_return"],
                    "win_rate_minus_baseline": win_rate_minus_baseline,
                    "computed_at": computed_at,
                }
            )

    stored = _upsert_confluence_results(rows)
    logger.info("Stored %d confluence_backtest_results rows for %d pairs", stored, len(pair_conditions))

    return {"pairs_tested": len(pair_conditions), "rows_stored": stored, "results": results}


def _upsert_confluence_results(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(ConfluenceBacktestResult).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["signal_a", "signal_b", "forward_days"],
        set_={
            "sample_size": stmt.excluded.sample_size,
            "win_rate": stmt.excluded.win_rate,
            "mean_return": stmt.excluded.mean_return,
            "win_rate_minus_baseline": stmt.excluded.win_rate_minus_baseline,
            "computed_at": stmt.excluded.computed_at,
        },
    )
    with get_session() as session:
        session.execute(stmt)
    return len(rows)


if __name__ == "__main__":
    run_confluence_backtest()
