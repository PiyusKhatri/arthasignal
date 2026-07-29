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
    LiquidityStratifiedBacktestResult,
    MtfAgreementBacktestResult,
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

MTF_MULTI_DIMENSIONAL_DOWNGRADE_SIGNALS = {"marubozu_bullish"}
MTF_ACTIONABLE_SIGNALS = {"marubozu_bearish", "shooting_star"}
MTF_STRENGTHENS_SIGNALS = {"rsi_14 < 30 (oversold)"}

LIQUIDITY_INVERTED_SIGNALS = {"marubozu_bearish"}
LIQUIDITY_CAUTION_SIGNALS = {"shooting_star"}
LARGE_CAP_SIGNALS = {"doji", "marubozu_bullish"}
ROBUST_LIQUIDITY_SIGNALS = {"close < bollinger_lower", "rsi_14 < 30 (oversold)"}


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


def _load_liquidity_stratified() -> dict[str, dict[str, list[Decimal]]]:
    with get_session() as session:
        rows = session.execute(select(LiquidityStratifiedBacktestResult)).scalars().all()
        by_signal: dict[str, dict[str, list[Decimal]]] = {}
        for row in rows:
            if row.win_rate_minus_baseline is None:
                continue
            by_signal.setdefault(row.signal_name, {}).setdefault(row.liquidity_tier, []).append(
                row.win_rate_minus_baseline
            )
        return by_signal


def _apply_liquidity_awareness(classifications: list[dict[str, Any]]) -> list[dict[str, Any]]:
    liquidity_by_signal = _load_liquidity_stratified()
    tracked_signals = (
        LIQUIDITY_INVERTED_SIGNALS | LIQUIDITY_CAUTION_SIGNALS | LARGE_CAP_SIGNALS | ROBUST_LIQUIDITY_SIGNALS
    )

    for c in classifications:
        if c["signal_name"] not in tracked_signals:
            continue

        tiers = liquidity_by_signal.get(c["signal_name"])
        required_tiers = ("high_liquidity", "medium_liquidity", "low_liquidity")
        if tiers is None or not all(t in tiers for t in required_tiers):
            continue

        avg = {t: sum(tiers[t]) / len(tiers[t]) for t in required_tiers}
        high, medium, low = avg["high_liquidity"], avg["medium_liquidity"], avg["low_liquidity"]

        if c["signal_name"] in LIQUIDITY_INVERTED_SIGNALS:
            c["tier"] = SignalConfidenceTier.LIQUIDITY_INVERTED
            c["liquidity_note"] = (
                f"LIQUIDITY-INVERTED: DOWNGRADED from high_confidence. Negative on high_liquidity "
                f"(avg win_rate_minus_baseline={high:.2f}), positive on low_liquidity (avg={low:.2f}) "
                f"and medium_liquidity (avg={medium:.2f}). The aggregate high_confidence label was "
                f"misleading - real edge is concentrated in harder-to-trade names. Do not recommend "
                f"this signal for large-cap/liquid stocks."
            )
        elif c["signal_name"] in LIQUIDITY_CAUTION_SIGNALS:
            c["liquidity_note"] = (
                f"CAUTION - liquidity-skewed: aggregate high_confidence is driven mainly by "
                f"low_liquidity performance (avg win_rate_minus_baseline={low:.2f}); high_liquidity "
                f"edge is weak (avg={high:.2f}) versus medium_liquidity (avg={medium:.2f}). Not "
                f"downgraded since high_liquidity is still positive, but do not recommend this signal "
                f"for large-cap/liquid stock trades specifically."
            )
        elif c["signal_name"] in LARGE_CAP_SIGNALS:
            c["liquidity_note"] = (
                f"LARGE-CAP SIGNAL: strongest specifically on high_liquidity names (avg "
                f"win_rate_minus_baseline={high:.2f} vs medium={medium:.2f}, low={low:.2f}). Good fit "
                f"for liquid/large-cap trading; comparatively weak or inconsistent elsewhere."
            )
        elif c["signal_name"] in ROBUST_LIQUIDITY_SIGNALS:
            strongest = max(avg, key=avg.get)
            strongest_note = (
                "not high_liquidity as might be assumed" if strongest != "high_liquidity" else "high_liquidity"
            )
            c["liquidity_note"] = (
                f"ROBUST ACROSS LIQUIDITY: positive on every tier (high={high:.2f}, medium={medium:.2f}, "
                f"low={low:.2f}), broadly usable regardless of a stock's liquidity. Strongest tier is "
                f"{strongest_note}."
            )

    return classifications


def _load_mtf_agreement() -> dict[str, dict[str, list[Decimal]]]:
    with get_session() as session:
        rows = session.execute(select(MtfAgreementBacktestResult)).scalars().all()
        by_signal: dict[str, dict[str, list[Decimal]]] = {}
        for row in rows:
            if row.win_rate_minus_baseline is None:
                continue
            by_signal.setdefault(row.signal_name, {}).setdefault(row.agreement_group, []).append(
                row.win_rate_minus_baseline
            )
        return by_signal


def _load_regime_stability_single(signal_name: str) -> tuple[Decimal, int, Decimal, int] | None:
    regime = _load_regime_stability().get(signal_name)
    if regime is None or "first_half" not in regime or "second_half" not in regime:
        return None
    fh_delta, fh_n = regime["first_half"]
    sh_delta, sh_n = regime["second_half"]
    if fh_delta is None or sh_delta is None:
        return None
    return fh_delta, fh_n, sh_delta, sh_n


def _apply_mtf_awareness(classifications: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mtf_by_signal = _load_mtf_agreement()
    liquidity_by_signal = _load_liquidity_stratified()
    tracked_signals = MTF_MULTI_DIMENSIONAL_DOWNGRADE_SIGNALS | MTF_ACTIONABLE_SIGNALS | MTF_STRENGTHENS_SIGNALS

    for c in classifications:
        if c["signal_name"] not in tracked_signals:
            continue

        groups = mtf_by_signal.get(c["signal_name"])
        required_groups = ("high_agreement", "low_agreement")
        if groups is None or not all(g in groups for g in required_groups):
            continue

        high_avg = sum(groups["high_agreement"]) / len(groups["high_agreement"])
        low_avg = sum(groups["low_agreement"]) / len(groups["low_agreement"])

        if c["signal_name"] in MTF_MULTI_DIMENSIONAL_DOWNGRADE_SIGNALS:
            regime = _load_regime_stability_single(c["signal_name"])
            liquidity_tiers = liquidity_by_signal.get(c["signal_name"])

            regime_part = "regime data unavailable"
            if regime is not None:
                fh_delta, fh_n, sh_delta, sh_n = regime
                regime_part = (
                    f"REGIME - decayed from first_half={fh_delta:.2f} (n={fh_n}) to "
                    f"second_half={sh_delta:.2f} (n={sh_n})"
                )

            liquidity_part = "liquidity data unavailable"
            if liquidity_tiers is not None and all(
                t in liquidity_tiers for t in ("high_liquidity", "medium_liquidity", "low_liquidity")
            ):
                liq_high = sum(liquidity_tiers["high_liquidity"]) / len(liquidity_tiers["high_liquidity"])
                liq_low = sum(liquidity_tiers["low_liquidity"]) / len(liquidity_tiers["low_liquidity"])
                liquidity_part = (
                    f"LIQUIDITY - strong on high_liquidity (avg={liq_high:.2f}), weak on low_liquidity "
                    f"(avg={liq_low:.2f}); only works on large-caps"
                )

            c["tier"] = SignalConfidenceTier.UNSTABLE_MULTI_DIMENSIONAL
            c["notes"] = (
                f"{c['notes']} | MULTI-DIMENSIONAL INSTABILITY: DOWNGRADED from decayed_edge. Fails or "
                f"inverts on 3 independent validation checks: (1) {regime_part}; (2) {liquidity_part}; "
                f"(3) MULTI-TIMEFRAME - inverted, low_agreement (avg={low_avg:.2f}) outperforms "
                f"high_agreement (avg={high_avg:.2f}). Treat as unreliable; do not use for trading "
                f"decisions without further investigation."
            )
        elif c["signal_name"] in MTF_ACTIONABLE_SIGNALS:
            c["mtf_note"] = (
                f"MULTI-TIMEFRAME CONFIRMS: flips from negative to positive when confirmed by "
                f"weekly/monthly agreement (avg win_rate_minus_baseline high_agreement={high_avg:.2f} vs "
                f"low_agreement={low_avg:.2f}). Actionable - require multi-timeframe confirmation "
                f"before trusting this signal."
            )
        elif c["signal_name"] in MTF_STRENGTHENS_SIGNALS:
            c["mtf_note"] = (
                f"MULTI-TIMEFRAME STRENGTHENS: positive either way, but stronger when confirmed (avg "
                f"high_agreement={high_avg:.2f} vs low_agreement={low_avg:.2f}). Not required, but "
                f"improves the edge when present."
            )

    return classifications


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
            "liquidity_note": stmt.excluded.liquidity_note,
            "mtf_note": stmt.excluded.mtf_note,
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
    classifications = _apply_liquidity_awareness(classifications)
    classifications = _apply_mtf_awareness(classifications)

    rows = [
        {
            "signal_name": c["signal_name"],
            "tier": c["tier"].value,
            "avg_win_rate_minus_baseline": c["avg_win_rate_minus_baseline"],
            "min_sample_size": c["min_sample_size"],
            "notes": c["notes"],
            "universe_note": SURVIVORSHIP_BIAS_NOTE,
            "liquidity_note": c.get("liquidity_note"),
            "mtf_note": c.get("mtf_note"),
        }
        for c in classifications
    ]
    stored = _upsert_signal_confidence(rows)
    logger.info("Stored %d signal_confidence rows", stored)
    return classifications


if __name__ == "__main__":
    compute_signal_confidence()
