from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import BacktestResult, SignalConfidence, SignalConfidenceTier
from src.pipeline.backtest_signals import DEFAULT_FORWARD_DAYS
from src.pipeline.compute_baseline import BASELINE_SIGNAL_NAME
from src.pipeline.run_signal_backtests import build_signal_conditions

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LOW_SAMPLE_THRESHOLD = 500
UNCLASSIFIED_TIER = "unclassified"


def get_signal_confidence(signal_name: str) -> dict[str, Any]:
    with get_session() as session:
        row = session.execute(
            select(SignalConfidence).where(SignalConfidence.signal_name == signal_name)
        ).scalar_one_or_none()

        if row is None:
            return {"signal_name": signal_name, "tier": UNCLASSIFIED_TIER, "avg_win_rate_minus_baseline": None}

        return {
            "signal_name": signal_name,
            "tier": row.tier.value,
            "avg_win_rate_minus_baseline": row.avg_win_rate_minus_baseline,
        }


def _load_results_by_signal_and_horizon() -> dict[str, dict[int, dict[str, Any]]]:
    with get_session() as session:
        rows = session.execute(select(BacktestResult)).scalars().all()
        session.expunge_all()

    by_signal: dict[str, dict[int, dict[str, Any]]] = {}
    for row in rows:
        by_signal.setdefault(row.signal_name, {})[row.forward_days] = {
            "sample_size": row.sample_size,
            "mean_return": row.mean_return,
            "win_rate": row.win_rate,
        }
    return by_signal


def _classify_signal(
    signal_name: str,
    per_horizon: dict[int, dict[str, Any]],
    baseline_per_horizon: dict[int, dict[str, Any]],
    forward_days: list[int],
) -> dict[str, Any]:
    deltas: dict[int, Decimal] = {}
    sample_sizes: dict[int, int] = {}

    for n in forward_days:
        signal_stats = per_horizon.get(n)
        baseline_stats = baseline_per_horizon.get(n)
        if signal_stats is None or baseline_stats is None or signal_stats["win_rate"] is None:
            continue
        deltas[n] = signal_stats["win_rate"] - baseline_stats["win_rate"]
        sample_sizes[n] = signal_stats["sample_size"]

    if len(deltas) < len(forward_days):
        missing = sorted(set(forward_days) - set(deltas))
        return {
            "signal_name": signal_name,
            "tier": SignalConfidenceTier.UNRELIABLE_LOW_SAMPLE,
            "avg_win_rate_minus_baseline": None,
            "min_sample_size": min(sample_sizes.values()) if sample_sizes else 0,
            "notes": f"missing backtest_results rows for forward_days={missing}",
        }

    min_sample_size = min(sample_sizes.values())
    avg_delta = sum(deltas.values()) / Decimal(len(deltas))
    per_horizon_desc = ", ".join(f"{n}d={'+' if deltas[n] > 0 else ('0' if deltas[n] == 0 else '-')}" for n in forward_days)

    if min_sample_size < LOW_SAMPLE_THRESHOLD:
        worst_n = min(sample_sizes, key=lambda n: sample_sizes[n])
        return {
            "signal_name": signal_name,
            "tier": SignalConfidenceTier.UNRELIABLE_LOW_SAMPLE,
            "avg_win_rate_minus_baseline": avg_delta,
            "min_sample_size": min_sample_size,
            "notes": f"sample_size={sample_sizes[worst_n]} at {worst_n}d, below {LOW_SAMPLE_THRESHOLD} threshold",
        }

    beats_baseline_flags = [deltas[n] > 0 for n in forward_days]
    is_consistent = all(beats_baseline_flags) or not any(beats_baseline_flags)

    if not is_consistent:
        return {
            "signal_name": signal_name,
            "tier": SignalConfidenceTier.INCONSISTENT_ACROSS_HORIZONS,
            "avg_win_rate_minus_baseline": avg_delta,
            "min_sample_size": min_sample_size,
            "notes": f"win_rate_minus_baseline sign flips across horizons: {per_horizon_desc}",
        }

    if all(beats_baseline_flags):
        return {
            "signal_name": signal_name,
            "tier": SignalConfidenceTier.HIGH_CONFIDENCE,
            "avg_win_rate_minus_baseline": avg_delta,
            "min_sample_size": min_sample_size,
            "notes": f"positive win_rate_minus_baseline at all horizons: {per_horizon_desc}",
        }

    return {
        "signal_name": signal_name,
        "tier": SignalConfidenceTier.WEAK_OR_NO_EDGE,
        "avg_win_rate_minus_baseline": avg_delta,
        "min_sample_size": min_sample_size,
        "notes": f"consistently at or below baseline: {per_horizon_desc}",
    }


def _upsert_signal_confidence(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(SignalConfidence).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["signal_name"],
        set_={
            "tier": stmt.excluded.tier,
            "avg_win_rate_minus_baseline": stmt.excluded.avg_win_rate_minus_baseline,
            "min_sample_size": stmt.excluded.min_sample_size,
            "notes": stmt.excluded.notes,
        },
    )
    with get_session() as session:
        session.execute(stmt)
    return len(rows)


def compute_signal_confidence(forward_days: list[int] = DEFAULT_FORWARD_DAYS) -> list[dict[str, Any]]:
    results_by_signal = _load_results_by_signal_and_horizon()
    baseline_per_horizon = results_by_signal.get(BASELINE_SIGNAL_NAME, {})

    signal_names = list(build_signal_conditions().keys())

    classifications = []
    for signal_name in signal_names:
        per_horizon = results_by_signal.get(signal_name, {})
        classifications.append(_classify_signal(signal_name, per_horizon, baseline_per_horizon, forward_days))

    rows = [
        {
            "signal_name": c["signal_name"],
            "tier": c["tier"].value,
            "avg_win_rate_minus_baseline": c["avg_win_rate_minus_baseline"],
            "min_sample_size": c["min_sample_size"],
            "notes": c["notes"],
        }
        for c in classifications
    ]
    stored = _upsert_signal_confidence(rows)
    logger.info("Stored %d signal_confidence rows", stored)
    return classifications


if __name__ == "__main__":
    compute_signal_confidence()
