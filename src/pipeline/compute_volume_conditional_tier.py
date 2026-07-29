from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import SignalConfidenceTier, VolumeConditionalTier, VolumeConfirmedBacktestResult

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LOW_SAMPLE_THRESHOLD = 500
FORWARD_DAYS = [5, 10, 20]

VOLUME_SPLIT_PATTERNS = [
    "dark_cloud_cover",
    "bearish_engulfing",
    "bullish_harami",
    "marubozu_bearish",
    "tweezer_bottom",
    "bullish_engulfing",
    "marubozu_bullish",
    "tweezer_top",
    "spinning_top",
    "shooting_star",
    "doji",
]


def _load_results() -> dict[tuple[str, str], dict[int, dict[str, Any]]]:
    with get_session() as session:
        rows = session.execute(
            select(VolumeConfirmedBacktestResult).where(
                VolumeConfirmedBacktestResult.pattern_name.in_(VOLUME_SPLIT_PATTERNS)
            )
        ).scalars().all()

        by_key: dict[tuple[str, str], dict[int, dict[str, Any]]] = {}
        for row in rows:
            key = (row.pattern_name, row.volume_condition)
            by_key.setdefault(key, {})[row.forward_days] = {
                "sample_size": row.sample_size,
                "win_rate_minus_baseline": row.win_rate_minus_baseline,
            }
        return by_key


def _classify(pattern_name: str, volume_condition: str, per_horizon: dict[int, dict[str, Any]]) -> dict[str, Any]:
    deltas: dict[int, Decimal] = {}
    sample_sizes: dict[int, int] = {}

    for n in FORWARD_DAYS:
        stats = per_horizon.get(n)
        if stats is None or stats["win_rate_minus_baseline"] is None:
            continue
        deltas[n] = stats["win_rate_minus_baseline"]
        sample_sizes[n] = stats["sample_size"]

    if len(deltas) < len(FORWARD_DAYS):
        missing = sorted(set(FORWARD_DAYS) - set(deltas))
        return {
            "pattern_name": pattern_name,
            "volume_condition": volume_condition,
            "tier": SignalConfidenceTier.UNRELIABLE_LOW_SAMPLE,
            "avg_win_rate_minus_baseline": None,
            "min_sample_size": min(sample_sizes.values()) if sample_sizes else 0,
            "notes": f"missing volume_confirmed_backtest_results rows for forward_days={missing}",
        }

    min_sample_size = min(sample_sizes.values())
    avg_delta = sum(deltas.values()) / Decimal(len(deltas))
    per_horizon_desc = ", ".join(
        f"{n}d={'+' if deltas[n] > 0 else ('0' if deltas[n] == 0 else '-')}({deltas[n]:.2f},n={sample_sizes[n]})"
        for n in FORWARD_DAYS
    )

    if min_sample_size < LOW_SAMPLE_THRESHOLD:
        worst_n = min(sample_sizes, key=lambda n: sample_sizes[n])
        return {
            "pattern_name": pattern_name,
            "volume_condition": volume_condition,
            "tier": SignalConfidenceTier.UNRELIABLE_LOW_SAMPLE,
            "avg_win_rate_minus_baseline": avg_delta,
            "min_sample_size": min_sample_size,
            "notes": f"sample_size={sample_sizes[worst_n]} at {worst_n}d, below {LOW_SAMPLE_THRESHOLD} threshold",
        }

    beats_baseline_flags = [deltas[n] > 0 for n in FORWARD_DAYS]
    is_consistent = all(beats_baseline_flags) or not any(beats_baseline_flags)

    if not is_consistent:
        return {
            "pattern_name": pattern_name,
            "volume_condition": volume_condition,
            "tier": SignalConfidenceTier.INCONSISTENT_ACROSS_HORIZONS,
            "avg_win_rate_minus_baseline": avg_delta,
            "min_sample_size": min_sample_size,
            "notes": f"win_rate_minus_baseline sign flips across horizons: {per_horizon_desc}",
        }

    if all(beats_baseline_flags):
        return {
            "pattern_name": pattern_name,
            "volume_condition": volume_condition,
            "tier": SignalConfidenceTier.HIGH_CONFIDENCE,
            "avg_win_rate_minus_baseline": avg_delta,
            "min_sample_size": min_sample_size,
            "notes": f"positive win_rate_minus_baseline at all horizons: {per_horizon_desc}",
        }

    return {
        "pattern_name": pattern_name,
        "volume_condition": volume_condition,
        "tier": SignalConfidenceTier.WEAK_OR_NO_EDGE,
        "avg_win_rate_minus_baseline": avg_delta,
        "min_sample_size": min_sample_size,
        "notes": f"consistently at or below baseline: {per_horizon_desc}",
    }


def _upsert(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(VolumeConditionalTier).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["pattern_name", "volume_condition"],
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


def compute_volume_conditional_tiers() -> list[dict[str, Any]]:
    results_by_key = _load_results()

    classifications = []
    for pattern_name in VOLUME_SPLIT_PATTERNS:
        for volume_condition in ("high", "normal"):
            per_horizon = results_by_key.get((pattern_name, volume_condition), {})
            classifications.append(_classify(pattern_name, volume_condition, per_horizon))

    rows = [
        {
            "pattern_name": c["pattern_name"],
            "volume_condition": c["volume_condition"],
            "tier": c["tier"].value,
            "avg_win_rate_minus_baseline": c["avg_win_rate_minus_baseline"],
            "min_sample_size": c["min_sample_size"],
            "notes": c["notes"],
        }
        for c in classifications
    ]
    stored = _upsert(rows)
    logger.info("Stored %d volume_conditional_tier rows", stored)
    return classifications


if __name__ == "__main__":
    compute_volume_conditional_tiers()
