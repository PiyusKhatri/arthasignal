"""Deterministic, fail-closed scoring primitives for the V5 challenger.

The module is intentionally independent of persistence and pipelines.  It
defines the frozen feature/policy contract that callers can train against and
use for forward inference without changing V4.1 or E1.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, replace
from statistics import mean, median
from typing import Any, Mapping, Sequence

from src.services.quant_residual_alpha import V4_META_FEATURE_NAMES, v4_feature_vector

V5_MODEL_VERSION = "artha-pit-stability-v5-research-v1"
V5_FEATURE_VERSION = "nepse-v5-pit-stability-v1"
V5_POLICY_VERSION = "2026-08-21-v5-research-v1"

V5_STABILITY_FEATURE_NAMES = (
    "trade_availability_20",
    "turnover_cv_20",
    "turnover_percentile",
    "baseline_percentile_median_5",
    "candidate_membership_count_5",
)
V5_STABILITY_FEATURE_SCALES = (1.0, 5.0, 1.0, 1.0, 5.0)
V5_FEATURE_NAMES = tuple(V4_META_FEATURE_NAMES) + V5_STABILITY_FEATURE_NAMES


@dataclass(frozen=True)
class V5Config:
    """Immutable model, feature, and policy contract."""

    model_version: str = V5_MODEL_VERSION
    feature_version: str = V5_FEATURE_VERSION
    policy_version: str = V5_POLICY_VERSION
    feature_names: tuple[str, ...] = V5_FEATURE_NAMES
    stability_feature_names: tuple[str, ...] = V5_STABILITY_FEATURE_NAMES
    stability_feature_scales: tuple[float, ...] = V5_STABILITY_FEATURE_SCALES
    composite_weights: tuple[float, float, float] = (0.50, 0.30, 0.20)
    score_shrinkage_weights: tuple[float, float] = (0.70, 0.30)
    prior_session_count: int = 5
    max_stability_age_sessions: int = 1


V5_CONFIG = V5Config()


class StabilityInputError(ValueError):
    """Raised when required V5 stability data cannot be used safely."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _unit_interval(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number in [0, 1]")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite number in [0, 1]") from error
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be a finite number in [0, 1]")
    return number


@dataclass(frozen=True)
class CalibratedOutputs:
    """The three calibrated signals supplied to the fixed V5 policy."""

    success_after_cost_probability: float
    within_date_alpha_signal: float
    severe_mae_safety_probability: float

    def __post_init__(self) -> None:
        for name in (
            "success_after_cost_probability",
            "within_date_alpha_signal",
            "severe_mae_safety_probability",
        ):
            object.__setattr__(self, name, _unit_interval(getattr(self, name), name))


@dataclass(frozen=True)
class ScoreObservation:
    """An unshrunk composite score recorded for one symbol/session."""

    date: Any
    symbol: str
    composite_score: float

    def __post_init__(self) -> None:
        if self.date is None:
            raise ValueError("score observation date is required")
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("score observation symbol is required")
        object.__setattr__(self, "symbol", self.symbol.strip())
        object.__setattr__(
            self,
            "composite_score",
            _unit_interval(self.composite_score, "composite_score"),
        )


@dataclass(frozen=True)
class ShrinkageResult:
    shrunk_score: float
    prior_session_scores: tuple[float, ...]


@dataclass(frozen=True)
class V5PolicyDecision:
    date: Any
    symbol: str
    status: str
    abstention_reason: str | None
    feature_vector: tuple[float, ...] | None
    calibrated_outputs: CalibratedOutputs
    baseline_percentile: float
    current_composite_score: float | None
    prior_session_scores: tuple[float, ...]
    shrunk_score: float | None
    within_date_rank: int | None
    model_version: str = V5_MODEL_VERSION
    feature_version: str = V5_FEATURE_VERSION
    policy_version: str = V5_POLICY_VERSION


@dataclass(frozen=True)
class CalibrationMetrics:
    samples: int
    brier_score: float
    expected_calibration_error: float
    bins: int


def _validate_stability_age(stability_age_sessions: int | None) -> None:
    if stability_age_sessions is None:
        raise StabilityInputError("missing_stability_age_sessions")
    if isinstance(stability_age_sessions, bool) or not isinstance(stability_age_sessions, int):
        raise StabilityInputError("invalid_stability_age_sessions")
    if stability_age_sessions < 0:
        raise StabilityInputError("invalid_stability_age_sessions")
    if stability_age_sessions > V5_CONFIG.max_stability_age_sessions:
        raise StabilityInputError("stale_stability_inputs")


def _stability_value(stability_features: Mapping[str, Any], name: str) -> float:
    if name not in stability_features or stability_features[name] is None:
        raise StabilityInputError(f"missing_stability_input:{name}")
    if isinstance(stability_features[name], bool):
        raise StabilityInputError(f"invalid_stability_input:{name}")
    try:
        value = float(stability_features[name])
    except (TypeError, ValueError) as error:
        raise StabilityInputError(f"invalid_stability_input:{name}") from error
    if not math.isfinite(value) or value < 0.0:
        raise StabilityInputError(f"invalid_stability_input:{name}")
    if name == "trade_availability_20" and value > 1.0:
        raise StabilityInputError(f"invalid_stability_input:{name}")
    if name in {"turnover_percentile", "baseline_percentile_median_5"} and value > 1.0:
        raise StabilityInputError(f"invalid_stability_input:{name}")
    if name == "candidate_membership_count_5":
        if value > 5.0 or not value.is_integer():
            raise StabilityInputError(f"invalid_stability_input:{name}")
    return min(value, 5.0) if name == "turnover_cv_20" else value


def build_v5_feature_vector(
    row: Mapping[str, Any],
    *,
    stability_age_sessions: int | None,
) -> tuple[float, ...]:
    """Return V4 ex-ante features followed by the five frozen stability fields.

    Freshness is inference metadata and is deliberately not a trained feature.
    Each stability feature is scaled to [0, 1] under a fixed ex-ante scale.
    Future outcomes and execution facts are never inspected.
    """
    _validate_stability_age(stability_age_sessions)
    stability_features = row.get("stability_features")
    if stability_features is None:
        raise StabilityInputError("missing_stability_features")
    if not isinstance(stability_features, Mapping):
        raise StabilityInputError("invalid_stability_features")
    stability = tuple(
        min(1.0, _stability_value(stability_features, name) / scale)
        for name, scale in zip(
            V5_CONFIG.stability_feature_names,
            V5_CONFIG.stability_feature_scales,
        )
    )
    return tuple(v4_feature_vector(dict(row))) + stability


def composite_score(outputs: CalibratedOutputs) -> float:
    """Apply the immutable 50/30/20 calibrated-output policy."""
    success_weight, alpha_weight, safety_weight = V5_CONFIG.composite_weights
    return (
        success_weight * outputs.success_after_cost_probability
        + alpha_weight * outputs.within_date_alpha_signal
        + safety_weight * outputs.severe_mae_safety_probability
    )


def _coerce_observation(value: ScoreObservation | Mapping[str, Any]) -> ScoreObservation:
    if isinstance(value, ScoreObservation):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("prior scores must be ScoreObservation or mapping values")
    return ScoreObservation(
        date=value.get("date"),
        symbol=str(value.get("symbol") or ""),
        composite_score=value.get("composite_score"),
    )


def shrink_current_score(
    current_score: float,
    *,
    trading_date: Any,
    symbol: str,
    prior_scores: Sequence[ScoreObservation | Mapping[str, Any]],
    prior_market_dates: Sequence[Any] = (),
) -> ShrinkageResult:
    """Shrink a current composite toward the prior five unshrunk composites.

    Same-date and future observations are ignored by construction.  This keeps
    the history feature strictly causal and avoids recursive smoothing.
    """
    current = _unit_interval(current_score, "current_score")
    observations = tuple(_coerce_observation(value) for value in prior_scores)
    seen: set[tuple[Any, str]] = set()
    for observation in observations:
        key = (observation.date, observation.symbol)
        if key in seen:
            raise ValueError(f"duplicate prior score: {observation.symbol} on {observation.date}")
        seen.add(key)

    allowed_dates = set(prior_market_dates[-V5_CONFIG.prior_session_count :])
    eligible = sorted(
        (
            observation
            for observation in observations
            if observation.symbol == symbol
            and observation.date < trading_date
            and observation.date in allowed_dates
        ),
        key=lambda observation: observation.date,
    )
    history = tuple(observation.composite_score for observation in eligible)
    if not history:
        return ShrinkageResult(shrunk_score=current, prior_session_scores=())

    current_weight, prior_weight = V5_CONFIG.score_shrinkage_weights
    return ShrinkageResult(
        shrunk_score=current_weight * current + prior_weight * median(history),
        prior_session_scores=history,
    )


def _coerce_outputs(value: CalibratedOutputs | Mapping[str, Any]) -> CalibratedOutputs:
    if isinstance(value, CalibratedOutputs):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("outputs must be CalibratedOutputs or mapping values")
    return CalibratedOutputs(
        success_after_cost_probability=value.get("success_after_cost_probability"),
        within_date_alpha_signal=value.get("within_date_alpha_signal"),
        severe_mae_safety_probability=value.get("severe_mae_safety_probability"),
    )


def score_v5_policy(
    rows: Sequence[Mapping[str, Any]],
    outputs: Sequence[CalibratedOutputs | Mapping[str, Any]],
    *,
    stability_age_sessions: int | None,
    prior_scores: Sequence[ScoreObservation | Mapping[str, Any]] = (),
    prior_market_dates: Sequence[Any] = (),
) -> tuple[V5PolicyDecision, ...]:
    """Score and deterministically rank candidates, abstaining on unsafe inputs."""
    if len(rows) != len(outputs):
        raise ValueError("rows and outputs must have equal length")

    candidates: list[tuple[Mapping[str, Any], CalibratedOutputs]] = []
    seen: set[tuple[Any, str]] = set()
    for row, raw_outputs in zip(rows, outputs):
        trading_date = row.get("date")
        symbol = str(row.get("symbol") or "").strip()
        if trading_date is None or not symbol:
            raise ValueError("each candidate requires date and symbol")
        key = (trading_date, symbol)
        if key in seen:
            raise ValueError(f"duplicate candidate: {symbol} on {trading_date}")
        seen.add(key)
        candidates.append((row, _coerce_outputs(raw_outputs)))

    decisions: list[V5PolicyDecision] = []
    for row, calibrated in candidates:
        trading_date = row["date"]
        symbol = str(row["symbol"]).strip()
        try:
            vector = build_v5_feature_vector(
                row,
                stability_age_sessions=stability_age_sessions,
            )
        except StabilityInputError as error:
            decisions.append(
                V5PolicyDecision(
                    date=trading_date,
                    symbol=symbol,
                    status="abstained",
                    abstention_reason=error.reason,
                    feature_vector=None,
                    calibrated_outputs=calibrated,
                    baseline_percentile=max(
                        0.0,
                        min(1.0, float(row.get("baseline_percentile") or 0.0)),
                    ),
                    current_composite_score=None,
                    prior_session_scores=(),
                    shrunk_score=None,
                    within_date_rank=None,
                )
            )
            continue

        current = composite_score(calibrated)
        shrinkage = shrink_current_score(
            current,
            trading_date=trading_date,
            symbol=symbol,
            prior_scores=prior_scores,
            prior_market_dates=prior_market_dates,
        )
        decisions.append(
            V5PolicyDecision(
                date=trading_date,
                symbol=symbol,
                status="scored",
                abstention_reason=None,
                feature_vector=vector,
                calibrated_outputs=calibrated,
                baseline_percentile=max(
                    0.0,
                    min(1.0, float(row.get("baseline_percentile") or 0.0)),
                ),
                current_composite_score=current,
                prior_session_scores=shrinkage.prior_session_scores,
                shrunk_score=shrinkage.shrunk_score,
                within_date_rank=None,
            )
        )

    grouped: dict[Any, list[V5PolicyDecision]] = defaultdict(list)
    for decision in decisions:
        if decision.status == "scored":
            grouped[decision.date].append(decision)

    ranked: list[V5PolicyDecision] = []
    for decision in decisions:
        if decision.status != "scored":
            ranked.append(decision)
            continue
        ordered = sorted(
            grouped[decision.date],
            key=lambda item: (
                -round(float(item.shrunk_score), 12),
                -float(item.current_composite_score),
                -item.baseline_percentile,
                item.symbol,
            ),
        )
        rank = next(index for index, item in enumerate(ordered, start=1) if item.symbol == decision.symbol)
        ranked.append(replace(decision, within_date_rank=rank))

    return tuple(
        sorted(
            ranked,
            key=lambda item: (
                item.date,
                item.within_date_rank is None,
                item.within_date_rank or 0,
                item.symbol,
            ),
        )
    )


def _validated_calibration_inputs(
    probabilities: Sequence[float],
    outcomes: Sequence[bool | int | float],
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    if not probabilities:
        raise ValueError("calibration inputs must be non-empty")
    if len(probabilities) != len(outcomes):
        raise ValueError("probabilities and outcomes must have equal length")
    checked_probabilities = tuple(
        _unit_interval(value, "probabilities") for value in probabilities
    )
    checked_outcomes: list[float] = []
    for value in outcomes:
        if value not in (0, 1, False, True):
            raise ValueError("outcomes must contain only binary 0/1 values")
        checked_outcomes.append(float(value))
    return checked_probabilities, tuple(checked_outcomes)


def brier_score(
    probabilities: Sequence[float],
    outcomes: Sequence[bool | int | float],
) -> float:
    """Return mean squared probability error for binary outcomes."""
    checked_probabilities, checked_outcomes = _validated_calibration_inputs(
        probabilities,
        outcomes,
    )
    return mean(
        (probability - outcome) ** 2
        for probability, outcome in zip(checked_probabilities, checked_outcomes)
    )


def expected_calibration_error(
    probabilities: Sequence[float],
    outcomes: Sequence[bool | int | float],
    *,
    bins: int = 10,
) -> float:
    """Return equal-width expected calibration error (ECE)."""
    if isinstance(bins, bool) or not isinstance(bins, int) or bins <= 0:
        raise ValueError("bins must be a positive integer")
    checked_probabilities, checked_outcomes = _validated_calibration_inputs(
        probabilities,
        outcomes,
    )
    grouped: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for probability, outcome in zip(checked_probabilities, checked_outcomes):
        index = min(int(probability * bins), bins - 1)
        grouped[index].append((probability, outcome))
    samples = len(checked_probabilities)
    return sum(
        len(values)
        / samples
        * abs(
            mean(probability for probability, _ in values)
            - mean(outcome for _, outcome in values)
        )
        for values in grouped.values()
    )


def calibration_metrics(
    probabilities: Sequence[float],
    outcomes: Sequence[bool | int | float],
    *,
    bins: int = 10,
) -> CalibrationMetrics:
    """Compute the frozen confidence-policy calibration inputs together."""
    return CalibrationMetrics(
        samples=len(probabilities),
        brier_score=brier_score(probabilities, outcomes),
        expected_calibration_error=expected_calibration_error(
            probabilities,
            outcomes,
            bins=bins,
        ),
        bins=bins,
    )


def calibration_confidence(
    metrics: CalibrationMetrics,
    *,
    target_samples: int = 200,
) -> float:
    """Map calibration quality and sample adequacy to a bounded confidence."""
    if isinstance(target_samples, bool) or not isinstance(target_samples, int) or target_samples <= 0:
        raise ValueError("target_samples must be a positive integer")
    sample_factor = min(1.0, metrics.samples / target_samples)
    brier_factor = max(0.0, 1.0 - 2.0 * _unit_interval(metrics.brier_score, "brier_score"))
    ece_factor = max(
        0.0,
        1.0 - _unit_interval(metrics.expected_calibration_error, "expected_calibration_error"),
    )
    return sample_factor * brier_factor * ece_factor
