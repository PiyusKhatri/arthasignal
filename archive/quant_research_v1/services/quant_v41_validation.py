from __future__ import annotations

import random
from collections import defaultdict
from statistics import mean, median
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.quant_models import QuantV41ShadowRun, QuantV41ShadowSignal
from archive.quant_research_v1.services.quant_residual_alpha import V4_EXECUTION_HURDLE_PERCENT
from archive.quant_research_v1.services.quant_v41_artifact import V41_FROZEN_MODEL_VERSION

V41_FORWARD_POLICY_VERSION = "2026-08-21-v41-forward-v1"
V41_FORWARD_BOOTSTRAP_ITERATIONS = 2000
V41_FORWARD_BOOTSTRAP_SEED = 20260821
MIN_MATCHED_DATES = 20
MIN_RESOLVED_V41_CALLS = 80
MIN_NON_OVERLAP_COHORTS = 12
MIN_RISK_REDUCTION = 0.10


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = max(0.0, min(1.0, float(fraction))) * (len(ordered) - 1)
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _bootstrap_mean(values: Sequence[float], *, seed: int) -> dict[str, Any]:
    if not values:
        return {"iterations": 0, "low": None, "high": None}
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(V41_FORWARD_BOOTSTRAP_ITERATIONS):
        sample = [float(values[rng.randrange(len(values))]) for _ in range(len(values))]
        samples.append(mean(sample))
    return {
        "iterations": V41_FORWARD_BOOTSTRAP_ITERATIONS,
        "low": _percentile(samples, 0.025),
        "high": _percentile(samples, 0.975),
    }


def _group_resolved(rows: Sequence[QuantV41ShadowSignal]) -> dict[Any, list[QuantV41ShadowSignal]]:
    grouped: dict[Any, list[QuantV41ShadowSignal]] = defaultdict(list)
    for row in rows:
        if row.status == "resolved":
            grouped[row.as_of_date].append(row)
    return grouped


def _date_comparisons(rows: Sequence[QuantV41ShadowSignal]) -> list[dict[str, Any]]:
    comparisons: list[dict[str, Any]] = []
    for trading_date, date_rows in sorted(_group_resolved(rows).items()):
        v41 = [row for row in date_rows if row.selected_v41]
        baseline = [row for row in date_rows if row.selected_baseline]
        if not v41 or len(v41) != len(baseline):
            continue
        if any(row.realized_excess_return_percent is None for row in [*v41, *baseline]):
            continue
        v41_net = mean(float(row.realized_excess_return_percent) - V4_EXECUTION_HURDLE_PERCENT for row in v41)
        baseline_net = mean(float(row.realized_excess_return_percent) - V4_EXECUTION_HURDLE_PERCENT for row in baseline)
        resolution_dates = [row.resolution_date for row in [*v41, *baseline] if row.resolution_date is not None]
        comparisons.append(
            {
                "date": trading_date,
                "breadth": len(v41),
                "v41_net_excess_percent": v41_net,
                "baseline_net_excess_percent": baseline_net,
                "incremental_alpha_percent": v41_net - baseline_net,
                "resolution_date": max(resolution_dates) if resolution_dates else None,
            }
        )
    return comparisons


def _non_overlapping(comparisons: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    cohorts: list[dict[str, Any]] = []
    active_until = None
    for row in sorted(comparisons, key=lambda item: item["date"]):
        if active_until is not None and row["date"] <= active_until:
            continue
        if row.get("resolution_date") is None:
            continue
        cohorts.append(row)
        active_until = row["resolution_date"]
    return cohorts


def _heartbeat_summary(runs: Sequence[QuantV41ShadowRun]) -> dict[str, Any]:
    by_date: dict[Any, list[QuantV41ShadowRun]] = defaultdict(list)
    for run in runs:
        by_date[run.as_of_date].append(run)
    successful_dates = {
        trading_date
        for trading_date, date_runs in by_date.items()
        if any(run.run_status in {"captured", "abstained"} for run in date_runs)
    }
    failed_only_dates = {
        trading_date
        for trading_date, date_runs in by_date.items()
        if date_runs and not any(run.run_status in {"captured", "abstained"} for run in date_runs)
    }
    latest = runs[-1] if runs else None
    return {
        "attempts": len(runs),
        "unique_market_dates": len(by_date),
        "successful_market_dates": len(successful_dates),
        "failed_only_market_dates": len(failed_only_dates),
        "captured_attempts": sum(run.run_status == "captured" for run in runs),
        "abstained_attempts": sum(run.run_status == "abstained" for run in runs),
        "failed_attempts": sum(run.run_status == "failed" for run in runs),
        "latest": (
            {
                "as_of_date": latest.as_of_date.isoformat(),
                "run_status": latest.run_status,
                "candidate_rows": latest.candidate_rows,
                "v41_selected": latest.v41_selected,
                "baseline_selected": latest.baseline_selected,
                "run_fingerprint": latest.run_fingerprint,
                "created_at": latest.created_at.isoformat(),
            }
            if latest is not None
            else None
        ),
    }


def build_v41_forward_validation_status(session: Session) -> dict[str, Any]:
    rows = session.execute(
        select(QuantV41ShadowSignal)
        .where(QuantV41ShadowSignal.model_version == V41_FROZEN_MODEL_VERSION)
        .order_by(QuantV41ShadowSignal.as_of_date, QuantV41ShadowSignal.symbol)
    ).scalars().all()
    runs = session.execute(
        select(QuantV41ShadowRun)
        .where(QuantV41ShadowRun.model_version == V41_FROZEN_MODEL_VERSION)
        .order_by(QuantV41ShadowRun.as_of_date, QuantV41ShadowRun.created_at, QuantV41ShadowRun.id)
    ).scalars().all()

    pending = sum(row.status == "pending" for row in rows)
    resolved = [row for row in rows if row.status == "resolved"]
    voided = sum(row.status == "void" for row in rows)
    resolved_v41 = [row for row in resolved if row.selected_v41]
    resolved_baseline = [row for row in resolved if row.selected_baseline]
    comparisons = _date_comparisons(rows)
    matched_dates = {row["date"] for row in comparisons}
    matched_v41 = [row for row in resolved_v41 if row.as_of_date in matched_dates]
    matched_baseline = [row for row in resolved_baseline if row.as_of_date in matched_dates]
    increments = [float(row["incremental_alpha_percent"]) for row in comparisons]
    non_overlap = _non_overlapping(comparisons)
    non_overlap_increments = [float(row["incremental_alpha_percent"]) for row in non_overlap]

    v41_adverse = [
        abs(min(0.0, float(row.realized_mae_percent)))
        for row in matched_v41
        if row.realized_mae_percent is not None
    ]
    baseline_adverse = [
        abs(min(0.0, float(row.realized_mae_percent)))
        for row in matched_baseline
        if row.realized_mae_percent is not None
    ]
    v41_severe = mean(1.0 if value >= 5.0 else 0.0 for value in v41_adverse) if v41_adverse else None
    baseline_severe = mean(1.0 if value >= 5.0 else 0.0 for value in baseline_adverse) if baseline_adverse else None
    risk_reduction = (
        1.0 - float(v41_severe) / float(baseline_severe)
        if v41_severe is not None and baseline_severe not in (None, 0.0)
        else None
    )

    date_bootstrap = _bootstrap_mean(increments, seed=V41_FORWARD_BOOTSTRAP_SEED)
    non_overlap_bootstrap = _bootstrap_mean(non_overlap_increments, seed=V41_FORWARD_BOOTSTRAP_SEED + 17)
    checks = {
        "enough_resolved_v41_calls": len(matched_v41) >= MIN_RESOLVED_V41_CALLS,
        "enough_matched_dates": len(comparisons) >= MIN_MATCHED_DATES,
        "mean_incremental_alpha_positive": bool(increments) and mean(increments) > 0.0,
        "median_incremental_alpha_non_negative": bool(increments) and median(increments) >= 0.0,
        "date_bootstrap_lower_bound_positive": date_bootstrap.get("low") is not None and float(date_bootstrap["low"]) > 0.0,
        "enough_non_overlap_cohorts": len(non_overlap) >= MIN_NON_OVERLAP_COHORTS,
        "non_overlap_bootstrap_lower_bound_positive": (
            non_overlap_bootstrap.get("low") is not None and float(non_overlap_bootstrap["low"]) > 0.0
        ),
        "risk_reduction_at_least_10pct": risk_reduction is not None and risk_reduction >= MIN_RISK_REDUCTION,
    }
    mature = checks["enough_resolved_v41_calls"] and checks["enough_matched_dates"] and checks["enough_non_overlap_cohorts"]
    passed = mature and all(checks.values())

    return {
        "policy_version": V41_FORWARD_POLICY_VERSION,
        "model_version": V41_FROZEN_MODEL_VERSION,
        "status": "pass" if passed else ("review" if mature else "collecting"),
        "public_promotion_automatic": False,
        "heartbeat": _heartbeat_summary(runs),
        "ledger": {
            "rows": len(rows),
            "pending": pending,
            "resolved": len(resolved),
            "voided": voided,
            "resolved_v41_calls": len(resolved_v41),
            "resolved_baseline_calls": len(resolved_baseline),
            "matched_resolved_v41_calls": len(matched_v41),
            "matched_resolved_baseline_calls": len(matched_baseline),
            "matched_dates": len(comparisons),
            "non_overlap_cohorts": len(non_overlap),
        },
        "incremental_alpha": {
            "mean_percent": mean(increments) if increments else None,
            "median_percent": median(increments) if increments else None,
            "positive_date_rate": mean(1.0 if value > 0.0 else 0.0 for value in increments) if increments else None,
            "date_bootstrap_95": date_bootstrap,
            "non_overlap_mean_percent": mean(non_overlap_increments) if non_overlap_increments else None,
            "non_overlap_bootstrap_95": non_overlap_bootstrap,
        },
        "risk": {
            "sample_scope": "only fully resolved equal-breadth matched dates",
            "v41_severe_drawdown_rate": v41_severe,
            "baseline_severe_drawdown_rate": baseline_severe,
            "relative_risk_reduction": risk_reduction,
            "v41_mean_mae_percent": mean(v41_adverse) if v41_adverse else None,
            "baseline_mean_mae_percent": mean(baseline_adverse) if baseline_adverse else None,
        },
        "checks": checks,
        "note": (
            "This forward gate never promotes V4.1 automatically. A pass only means the frozen challenger has earned "
            "manual review as a possible successor to the current live champion. Heartbeat rows are append-only audit "
            "evidence and do not alter the statistical gate."
        ),
    }
