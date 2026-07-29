from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import (
    BacktestResult,
    ConfluenceConfidence,
    SignalConfidence,
    SignalConfidenceTier,
    SignalRegimeStability,
)
from src.pipeline.backtest_signals import DEFAULT_FORWARD_DAYS
from src.pipeline.compute_baseline import BASELINE_SIGNAL_NAME
from src.pipeline.run_signal_backtests import build_signal_conditions

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LOW_SAMPLE_THRESHOLD = 500
UNCLASSIFIED_TIER = "unclassified"

SURVIVORSHIP_BIAS_NOTE = (
    "SURVIVORSHIP BIAS CAVEAT: backtest universe = 287 currently-active equities + 35 recovered "
    "delisted/suspended equities (of 209 identified via nepse_api; sharesansar historical coverage "
    "recovered only 16.7%). 174 delisted/suspended equities remain unrecoverable from available "
    "sources. Recovered symbols added ~5% of total company-days and shifted win_rate_minus_baseline "
    "by <0.6pts for every tested signal (immaterial). Results remain survivor-biased overall."
)

REGIME_FIRST_HALF_LABEL = "2021-07-25 to 2024-01-23"
REGIME_SECOND_HALF_LABEL = "2024-01-23 to 2026-07-23"

DECAY_DOWNGRADE_SIGNALS = {
    "close > bollinger_upper",
    "marubozu_bullish",
    "stochastic_k > 80",
}

CONFLUENCE_REGIME_PAIRS = [
    ("close < bollinger_lower", "rsi_14 < 30 (oversold)"),
    ("close < bollinger_lower", "marubozu_bearish"),
]


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


def _load_regime_stability() -> dict[str, dict[str, tuple[Decimal, int]]]:
    with get_session() as session:
        rows = session.execute(select(SignalRegimeStability)).scalars().all()
        by_signal: dict[str, dict[str, tuple[Decimal, int]]] = {}
        for row in rows:
            by_signal.setdefault(row.signal_name, {})[row.period] = (row.win_rate_minus_baseline, row.sample_size)
        return by_signal


def _apply_regime_awareness(classifications: list[dict[str, Any]]) -> list[dict[str, Any]]:
    regime_by_signal = _load_regime_stability()

    for c in classifications:
        if c["tier"] != SignalConfidenceTier.HIGH_CONFIDENCE:
            continue

        regime = regime_by_signal.get(c["signal_name"])
        if regime is None or "first_half" not in regime or "second_half" not in regime:
            continue

        fh_delta, fh_n = regime["first_half"]
        sh_delta, sh_n = regime["second_half"]
        if fh_delta is None or sh_delta is None:
            continue

        if c["signal_name"] in DECAY_DOWNGRADE_SIGNALS:
            c["tier"] = SignalConfidenceTier.DECAYED_EDGE
            c["notes"] = (
                f"{c['notes']} | DOWNGRADED from high_confidence: edge decayed in recent data - "
                f"first_half({REGIME_FIRST_HALF_LABEL}) win_rate_minus_baseline={fh_delta:.2f} (n={fh_n}) vs "
                f"second_half({REGIME_SECOND_HALF_LABEL}) win_rate_minus_baseline={sh_delta:.2f} (n={sh_n})"
            )
        else:
            c["notes"] = (
                f"{c['notes']} | regime check: first_half={fh_delta:.2f} (n={fh_n}), "
                f"second_half={sh_delta:.2f} (n={sh_n}); based on a single 2-way historical split, "
                f"not multiple confirmations"
            )

    return classifications


def _apply_regime_notes_to_confluence() -> None:
    regime_by_signal = _load_regime_stability()

    with get_session() as session:
        for signal_a, signal_b in CONFLUENCE_REGIME_PAIRS:
            pair_key = f"{signal_a} & {signal_b}"
            regime = regime_by_signal.get(pair_key)
            if regime is None or "first_half" not in regime or "second_half" not in regime:
                continue

            fh_delta, fh_n = regime["first_half"]
            sh_delta, sh_n = regime["second_half"]
            if fh_delta is None or sh_delta is None:
                continue

            row = session.execute(
                select(ConfluenceConfidence).where(
                    ConfluenceConfidence.signal_a == signal_a, ConfluenceConfidence.signal_b == signal_b
                )
            ).scalar_one_or_none()
            if row is None:
                continue

            regime_note = (
                f"regime check: first_half={fh_delta:.2f} (n={fh_n}), second_half={sh_delta:.2f} (n={sh_n}); "
                f"based on a single 2-way historical split, not multiple confirmations"
            )
            if regime_note not in (row.notes or ""):
                row.notes = f"{row.notes} | {regime_note}"


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
            "universe_note": stmt.excluded.universe_note,
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

    classifications = _apply_regime_awareness(classifications)
    _apply_regime_notes_to_confluence()

    rows = [
        {
            "signal_name": c["signal_name"],
            "tier": c["tier"].value,
            "avg_win_rate_minus_baseline": c["avg_win_rate_minus_baseline"],
            "min_sample_size": c["min_sample_size"],
            "notes": c["notes"],
            "universe_note": SURVIVORSHIP_BIAS_NOTE,
        }
        for c in classifications
    ]
    stored = _upsert_signal_confidence(rows)
    logger.info("Stored %d signal_confidence rows", stored)
    return classifications


if __name__ == "__main__":
    compute_signal_confidence()
