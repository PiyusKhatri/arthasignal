from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import ConfluenceBacktestResult, ConfluenceConfidence, ConfluenceConfidenceTier
from src.pipeline.backtest_signals import DEFAULT_FORWARD_DAYS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LOW_SAMPLE_THRESHOLD = 500

GENUINE_PAIRS = [
    ("marubozu_bearish", "rsi_14 < 30 (oversold)"),
    ("marubozu_bullish", "rsi_14 > 70 (overbought)"),
    ("close < bollinger_lower", "marubozu_bearish"),
    ("close > bollinger_upper", "doji"),
    ("close > bollinger_upper", "marubozu_bullish"),
    ("close < bollinger_lower", "rsi_14 < 30 (oversold)"),
]


def _load_pair_results(signal_a: str, signal_b: str) -> dict[int, dict[str, Any]]:
    with get_session() as session:
        rows = session.execute(
            select(ConfluenceBacktestResult).where(
                ConfluenceBacktestResult.signal_a == signal_a,
                ConfluenceBacktestResult.signal_b == signal_b,
            )
        ).scalars().all()
        return {
            r.forward_days: {"sample_size": r.sample_size, "win_rate_minus_baseline": r.win_rate_minus_baseline}
            for r in rows
        }


def _classify_pair(
    signal_a: str, signal_b: str, per_horizon: dict[int, dict[str, Any]], forward_days: list[int]
) -> dict[str, Any]:
    deltas: dict[int, Decimal] = {}
    sample_sizes: dict[int, int] = {}

    for n in forward_days:
        stats = per_horizon.get(n)
        if stats is None or stats["win_rate_minus_baseline"] is None:
            continue
        deltas[n] = stats["win_rate_minus_baseline"]
        sample_sizes[n] = stats["sample_size"]

    if len(deltas) < len(forward_days):
        missing = sorted(set(forward_days) - set(deltas))
        return {
            "signal_a": signal_a,
            "signal_b": signal_b,
            "tier": ConfluenceConfidenceTier.INCONSISTENT,
            "avg_win_rate_minus_baseline": None,
            "min_sample_size": min(sample_sizes.values()) if sample_sizes else 0,
            "notes": f"missing confluence_backtest_results rows for forward_days={missing}",
        }

    min_sample_size = min(sample_sizes.values())
    avg_delta = sum(deltas.values()) / Decimal(len(deltas))
    per_horizon_desc = ", ".join(
        f"{n}d={'+' if deltas[n] > 0 else ('0' if deltas[n] == 0 else '-')}({deltas[n]:.2f},n={sample_sizes[n]})"
        for n in forward_days
    )

    is_consistent_positive = all(deltas[n] > 0 for n in forward_days)

    if not is_consistent_positive:
        return {
            "signal_a": signal_a,
            "signal_b": signal_b,
            "tier": ConfluenceConfidenceTier.INCONSISTENT,
            "avg_win_rate_minus_baseline": avg_delta,
            "min_sample_size": min_sample_size,
            "notes": f"win_rate_minus_baseline not positive at every horizon: {per_horizon_desc}",
        }

    if min_sample_size < LOW_SAMPLE_THRESHOLD:
        return {
            "signal_a": signal_a,
            "signal_b": signal_b,
            "tier": ConfluenceConfidenceTier.CONSISTENT_LOW_SAMPLE,
            "avg_win_rate_minus_baseline": avg_delta,
            "min_sample_size": min_sample_size,
            "notes": f"positive at all horizons but n<{LOW_SAMPLE_THRESHOLD} somewhere: {per_horizon_desc}",
        }

    return {
        "signal_a": signal_a,
        "signal_b": signal_b,
        "tier": ConfluenceConfidenceTier.CONSISTENT_HIGH_CONFIDENCE,
        "avg_win_rate_minus_baseline": avg_delta,
        "min_sample_size": min_sample_size,
        "notes": f"positive win_rate_minus_baseline at all horizons, n>={LOW_SAMPLE_THRESHOLD} throughout: {per_horizon_desc}",
    }


def _upsert_confluence_confidence(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(ConfluenceConfidence).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["signal_a", "signal_b"],
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


def compute_confluence_confidence(forward_days: list[int] = DEFAULT_FORWARD_DAYS) -> list[dict[str, Any]]:
    classifications = []
    for signal_a, signal_b in GENUINE_PAIRS:
        per_horizon = _load_pair_results(signal_a, signal_b)
        classifications.append(_classify_pair(signal_a, signal_b, per_horizon, forward_days))

    rows = [
        {
            "signal_a": c["signal_a"],
            "signal_b": c["signal_b"],
            "tier": c["tier"].value,
            "avg_win_rate_minus_baseline": c["avg_win_rate_minus_baseline"],
            "min_sample_size": c["min_sample_size"],
            "notes": c["notes"],
        }
        for c in classifications
    ]
    stored = _upsert_confluence_confidence(rows)
    logger.info("Stored %d confluence_confidence rows", stored)
    return classifications


if __name__ == "__main__":
    compute_confluence_confidence()
