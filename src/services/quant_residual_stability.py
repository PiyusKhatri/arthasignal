from __future__ import annotations

import math
from bisect import bisect_left
from collections import defaultdict
from statistics import mean, median
from typing import Any, Sequence

from src.services.quant_residual_alpha import (
    V4_EXECUTION_HURDLE_PERCENT,
    V4_FINAL_CAPACITY,
)

V41_POLICY_VERSION = "2026-08-21-residual-stability-v1"
V41_MONOTONIC_BINS = 5
V41_MIN_CALIBRATION_ROWS = 60
V41_MAX_OVERRIDE = 0.20

# Fixed ex-ante override rules. V4.1 deliberately keeps the transparent baseline
# as the dominant ordering and lets ML make only bounded corrections.
V41_PROMOTE_PROBABILITY = 0.60
V41_PROMOTE_RESIDUAL_PERCENT = 1.00
V41_PROMOTE_MAX_MAE_PERCENT = 8.00
V41_PROMOTE_MIN_PAYOFF_RATIO = 1.00
V41_REJECT_PROBABILITY = 0.42
V41_REJECT_RESIDUAL_PERCENT = -1.50
V41_HARD_MAX_MAE_PERCENT = 12.00
V41_SOFT_MAX_MAE_PERCENT = 10.00
V41_SOFT_MAE_MIN_PAYOFF_RATIO = 1.25


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = _clamp(fraction, 0.0, 1.0) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def fit_monotonic_residual_calibrator(
    raw_predictions: Sequence[float],
    actual_residuals: Sequence[float],
    *,
    bins: int = V41_MONOTONIC_BINS,
) -> dict[str, Any] | None:
    """Fit a calibration-only monotonic map from raw residual score to realized residual.

    Raw XGBoost residual magnitude was useful in V4 but not monotonic across test
    buckets. V4.1 therefore bins *calibration* predictions, measures the median
    realized residual in each bin, and applies weighted PAVA so the final mapping
    cannot claim that a larger raw residual implies a smaller calibrated residual.
    No test outcomes are used here.
    """
    if len(raw_predictions) < V41_MIN_CALIBRATION_ROWS or len(raw_predictions) != len(actual_residuals):
        return None

    pairs = sorted(
        (float(prediction), float(actual))
        for prediction, actual in zip(raw_predictions, actual_residuals)
    )
    bins = max(3, min(int(bins), 8, len(pairs) // 20))
    if bins < 3:
        return None

    chunk_size = int(math.ceil(len(pairs) / bins))
    raw_bins: list[dict[str, float]] = []
    for start in range(0, len(pairs), chunk_size):
        chunk = pairs[start : start + chunk_size]
        if not chunk:
            continue
        raw_bins.append(
            {
                "upper": max(value for value, _ in chunk),
                "raw_center": median(value for value, _ in chunk),
                "realized": median(actual for _, actual in chunk),
                "weight": float(len(chunk)),
            }
        )
    if len(raw_bins) < 3:
        return None

    # Weighted pool-adjacent-violators algorithm.
    blocks: list[dict[str, float]] = []
    for index, item in enumerate(raw_bins):
        blocks.append(
            {
                "start": float(index),
                "end": float(index),
                "level": float(item["realized"]),
                "weight": float(item["weight"]),
            }
        )
        while len(blocks) >= 2 and blocks[-2]["level"] > blocks[-1]["level"]:
            right = blocks.pop()
            left = blocks.pop()
            total_weight = left["weight"] + right["weight"]
            blocks.append(
                {
                    "start": left["start"],
                    "end": right["end"],
                    "level": (
                        left["level"] * left["weight"] + right["level"] * right["weight"]
                    ) / total_weight,
                    "weight": total_weight,
                }
            )

    levels = [0.0] * len(raw_bins)
    for block in blocks:
        start = int(block["start"])
        end = int(block["end"])
        for index in range(start, end + 1):
            levels[index] = float(block["level"])

    return {
        "samples": len(pairs),
        "bins": len(raw_bins),
        "cutoffs": [float(item["upper"]) for item in raw_bins],
        "raw_centers": [float(item["raw_center"]) for item in raw_bins],
        "levels": levels,
        "raw_realized_medians": [float(item["realized"]) for item in raw_bins],
        "method": "calibration-only equal-frequency bins + weighted PAVA",
    }


def apply_monotonic_residual_calibrator(
    raw_prediction: float,
    calibrator: dict[str, Any] | None,
) -> float:
    if calibrator is None:
        return float(raw_prediction)
    cutoffs = [float(value) for value in calibrator.get("cutoffs", [])]
    levels = [float(value) for value in calibrator.get("levels", [])]
    if not cutoffs or len(cutoffs) != len(levels):
        return float(raw_prediction)
    index = bisect_left(cutoffs, float(raw_prediction))
    index = min(index, len(levels) - 1)
    return float(levels[index])


def build_v41_predictions(
    rows: Sequence[dict[str, Any]],
    residual_predictions: Sequence[float],
    residual_probabilities: Sequence[float],
    mae_predictions: Sequence[float],
    mfe_predictions: Sequence[float],
) -> list[dict[str, Any]]:
    if not (
        len(rows)
        == len(residual_predictions)
        == len(residual_probabilities)
        == len(mae_predictions)
        == len(mfe_predictions)
    ):
        return []

    result: list[dict[str, Any]] = []
    for row, residual, probability, mae, mfe in zip(
        rows,
        residual_predictions,
        residual_probabilities,
        mae_predictions,
        mfe_predictions,
    ):
        probability = _clamp(float(probability), 0.001, 0.999)
        residual = float(residual)
        mae = max(0.0, float(mae))
        mfe = max(0.0, float(mfe))
        payoff_ratio = mfe / max(1.0, mae)
        expected_excess = float(row.get("baseline_expected_excess_percent") or 0.0) + residual

        probability_edge = _clamp((probability - 0.50) / 0.20, -1.0, 1.0)
        residual_edge = _clamp(residual / 4.0, -1.0, 1.0)
        mae_penalty = _clamp(mae / 10.0, 0.0, 1.25)
        payoff_edge = _clamp((payoff_ratio - 1.0) / 1.50, -1.0, 1.0)

        promote = (
            probability >= V41_PROMOTE_PROBABILITY
            and residual >= V41_PROMOTE_RESIDUAL_PERCENT
            and mae <= V41_PROMOTE_MAX_MAE_PERCENT
            and payoff_ratio >= V41_PROMOTE_MIN_PAYOFF_RATIO
        )
        reject = (
            probability <= V41_REJECT_PROBABILITY
            or residual <= V41_REJECT_RESIDUAL_PERCENT
            or mae >= V41_HARD_MAX_MAE_PERCENT
            or (mae > V41_SOFT_MAX_MAE_PERCENT and payoff_ratio < V41_SOFT_MAE_MIN_PAYOFF_RATIO)
        )
        action = "reject" if reject else ("promote" if promote else "hold")

        # The baseline percentile remains the dominant signal. ML can only make a
        # bounded correction; this prevents the V3-style wholesale reordering that
        # lost to momentum in constrained regimes.
        correction = (
            0.10 * probability_edge
            + 0.08 * residual_edge
            + 0.04 * payoff_edge
            - 0.12 * mae_penalty
        )
        if promote:
            correction += 0.05
        if reject:
            correction -= 0.10
        correction = _clamp(correction, -V41_MAX_OVERRIDE, V41_MAX_OVERRIDE)
        baseline_percentile = _clamp(float(row.get("baseline_percentile") or 0.0), 0.0, 1.0)

        result.append(
            {
                "row": row,
                "predicted_residual_alpha_percent": residual,
                "probability_positive_residual": probability,
                "predicted_mae_percent": mae,
                "predicted_mfe_percent": mfe,
                "predicted_payoff_ratio": payoff_ratio,
                "expected_excess_return_percent": expected_excess,
                "baseline_percentile": baseline_percentile,
                "override_correction": correction,
                "override_action": action,
                "final_score": baseline_percentile + correction,
                "market_regime": row["v4_market_regime"],
                "sector_regime": row["v4_sector_regime"],
            }
        )
    return result


def _is_selectable(candidate: dict[str, Any]) -> bool:
    if candidate.get("override_action") == "reject":
        return False
    if V4_FINAL_CAPACITY.get(str(candidate.get("market_regime")), 0) <= 0:
        return False
    # Preserve the 1% execution objective, but allow a strong promotion to override
    # a slightly conservative baseline-expectation table.
    if float(candidate.get("expected_excess_return_percent") or 0.0) <= V4_EXECUTION_HURDLE_PERCENT:
        return candidate.get("override_action") == "promote"
    return True


def select_v41_setups(candidates: Sequence[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate["row"]["date"]].append(candidate)

    selected: list[dict[str, Any]] = []
    days: list[dict[str, Any]] = []
    for trading_date in sorted(grouped):
        date_candidates = grouped[trading_date]
        market = str(date_candidates[0]["market_regime"])
        capacity = V4_FINAL_CAPACITY.get(market, 0)
        selectable = [candidate for candidate in date_candidates if _is_selectable(candidate)]
        chosen = sorted(selectable, key=lambda candidate: float(candidate["final_score"]), reverse=True)[:capacity]
        selected.extend(chosen)
        days.append(
            {
                "date": trading_date,
                "market_regime": market,
                "candidate_pool": len(date_candidates),
                "selectable": len(selectable),
                "capacity": capacity,
                "selected": len(chosen),
            }
        )
    return {"selected": selected, "days": days}


def override_diagnostics(selection: dict[str, Any]) -> dict[str, Any]:
    selected = selection.get("selected", [])
    actions = {action: sum(candidate.get("override_action") == action for candidate in selected) for action in ("promote", "hold", "reject")}
    corrections = [float(candidate.get("override_correction") or 0.0) for candidate in selected]
    ranks = [float(candidate["row"].get("baseline_rank") or 0.0) for candidate in selected]
    return {
        "selected_rows": len(selected),
        "actions": actions,
        "mean_override_correction": mean(corrections) if corrections else None,
        "median_override_correction": median(corrections) if corrections else None,
        "mean_baseline_rank_selected": mean(ranks) if ranks else None,
    }


def _selection_group_stats(candidates: Sequence[dict[str, Any]]) -> dict[str, Any]:
    rows = [candidate["row"] for candidate in candidates]
    net = [float(row["excess_return_percent"]) - V4_EXECUTION_HURDLE_PERCENT for row in rows]
    adverse = [max(0.0, -float(row.get("max_adverse_percent") or 0.0)) for row in rows]
    return {
        "rows": len(rows),
        "dates": len({row["date"] for row in rows}),
        "mean_net_excess_percent": mean(net) if net else None,
        "median_net_excess_percent": median(net) if net else None,
        "precision_after_1pct": mean(1.0 if value > 0.0 else 0.0 for value in net) if net else None,
        "severe_drawdown_rate": mean(1.0 if value >= 5.0 else 0.0 for value in adverse) if adverse else None,
        "mean_mae_percent": mean(adverse) if adverse else None,
    }


def stability_breakdowns(selection: dict[str, Any]) -> dict[str, Any]:
    selected = selection.get("selected", [])
    dimensions = {
        "market_regime": lambda candidate: str(candidate.get("market_regime") or "unknown"),
        "sector": lambda candidate: str(candidate["row"].get("sector") or "unknown"),
        "baseline_rank_bucket": lambda candidate: str(candidate["row"].get("baseline_rank_bucket") or "unknown"),
        "liquidity": lambda candidate: str(candidate["row"].get("liquidity_bucket") or "unknown"),
        "override_action": lambda candidate: str(candidate.get("override_action") or "unknown"),
    }
    result: dict[str, Any] = {}
    for name, key_fn in dimensions.items():
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for candidate in selected:
            grouped[key_fn(candidate)].append(candidate)
        result[name] = {
            key: _selection_group_stats(group)
            for key, group in sorted(grouped.items())
        }
    return result


def calibration_diagnostics(
    rows: Sequence[dict[str, Any]],
    calibrated_residuals: Sequence[float],
    probabilities: Sequence[float],
) -> dict[str, Any]:
    specs = (
        ("negative", -100.0, 0.0),
        ("0_1", 0.0, 1.0),
        ("1_2", 1.0, 2.0),
        ("2_4", 2.0, 4.0),
        ("4_plus", 4.0, 100.0),
    )
    result: dict[str, Any] = {}
    for label, low, high in specs:
        values = [
            (row, float(prediction), float(probability))
            for row, prediction, probability in zip(rows, calibrated_residuals, probabilities)
            if low <= float(prediction) < high
        ]
        actuals = [float(row["residual_alpha_percent"]) for row, _, _ in values]
        result[label] = {
            "calls": len(values),
            "independent_dates": len({row["date"] for row, _, _ in values}),
            "mean_calibrated_residual_percent": mean(prediction for _, prediction, _ in values) if values else None,
            "mean_probability_positive_residual": mean(probability for _, _, probability in values) if values else None,
            "actual_positive_residual_rate": mean(1.0 if value > 0.0 else 0.0 for value in actuals) if actuals else None,
            "mean_actual_residual_percent": mean(actuals) if actuals else None,
            "median_actual_residual_percent": median(actuals) if actuals else None,
        }
    return result


def bootstrap_incremental_by_fold(values: Sequence[float], *, iterations: int = 1000, seed: int = 20260821) -> dict[str, float | None]:
    """Small helper used by fold diagnostics; deterministic and outcome-reporting only."""
    if not values:
        return {"low": None, "high": None}
    import random

    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(iterations):
        sample = [float(values[rng.randrange(len(values))]) for _ in range(len(values))]
        samples.append(mean(sample))
    return {"low": _percentile(samples, 0.025), "high": _percentile(samples, 0.975)}
