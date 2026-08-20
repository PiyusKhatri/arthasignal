"""Frozen, deterministic training primitives for the V5 research challenger.

This module owns only in-memory model fitting, held-out Platt calibration, and
outcome-free prediction.  Persistence, serialization, split construction, and
database concerns intentionally live elsewhere.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from src.services.quant_v5_model import (
    V5_CONFIG,
    CalibratedOutputs,
    build_v5_feature_vector,
)


@dataclass(frozen=True)
class V5TrainingConfig:
    """Immutable implementation details frozen by the V5 protocol."""

    seed: int = 20260821
    thread_count: int = 1
    ranker_rounds: int = 180
    classifier_rounds: int = 220
    platt_iterations: int = 500
    platt_learning_rate: float = 0.035
    platt_l2: float = 0.02
    minimum_rank_group_size: int = 10
    minimum_ranker_rows: int = 500
    minimum_ranker_dates: int = 20
    minimum_classifier_rows: int = 250
    minimum_classifier_class_rows: int = 40
    minimum_calibration_rows: int = 60
    minimum_calibration_class_rows: int = 15
    ranker_parameters: tuple[tuple[str, Any], ...] = (
        ("objective", "rank:ndcg"),
        ("eval_metric", "ndcg@10"),
        ("max_depth", 4),
        ("eta", 0.035),
        ("min_child_weight", 10),
        ("subsample", 0.80),
        ("colsample_bytree", 0.80),
        ("lambda", 3.0),
        ("alpha", 0.30),
        ("tree_method", "hist"),
        ("seed", 20260821),
        ("nthread", 1),
    )
    classifier_parameters: tuple[tuple[str, Any], ...] = (
        ("objective", "binary:logistic"),
        ("eval_metric", "logloss"),
        ("max_depth", 4),
        ("eta", 0.035),
        ("min_child_weight", 8),
        ("subsample", 0.78),
        ("colsample_bytree", 0.78),
        ("lambda", 2.5),
        ("alpha", 0.25),
        ("max_delta_step", 1),
        ("tree_method", "hist"),
        ("seed", 20260821),
        ("nthread", 1),
    )


V5_TRAINING_CONFIG = V5TrainingConfig()

# This allowlist is deliberately narrower than a full dataset row.  Inference
# projects into it before calling the shared feature builder, so copying a test
# row can never accidentally dereference a future outcome field.
_EX_ANTE_FEATURE_KEYS = (
    "features",
    "baseline_score",
    "baseline_percentile",
    "baseline_expected_excess_percent",
    "v4_sector_regime",
    "liquidity_bucket",
    "regime_context",
    "stability_features",
)


class V5TrainingError(ValueError):
    """Raised when a V5 head or calibrator cannot be fitted admissibly."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class PlattCalibrator:
    """Immutable regularized logistic map fitted on calibration rows only."""

    intercept: float
    slope: float
    samples: int

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.intercept)) or not math.isfinite(float(self.slope)):
            raise ValueError("Platt parameters must be finite")
        if isinstance(self.samples, bool) or self.samples < 1:
            raise ValueError("Platt sample count must be positive")

    def calibrate(self, probability: float) -> float:
        value = _probability(probability, "raw probability", allow_endpoints=True)
        bounded = min(1.0 - 1e-5, max(1e-5, value))
        logit = math.log(bounded / (1.0 - bounded))
        z = min(35.0, max(-35.0, self.intercept + self.slope * logit))
        return min(0.999, max(0.001, 1.0 / (1.0 + math.exp(-z))))


@dataclass(frozen=True)
class V5ModelBundle:
    """Immutable container for the three heads and two independent maps."""

    ranker: Any
    success_classifier: Any
    severe_mae_classifier: Any
    success_calibrator: PlattCalibrator
    severe_mae_calibrator: PlattCalibrator
    training_rows: int
    ranker_training_rows: int
    ranker_training_dates: int
    calibration_rows: int
    model_version: str = V5_CONFIG.model_version
    feature_version: str = V5_CONFIG.feature_version
    policy_version: str = V5_CONFIG.policy_version
    feature_names: tuple[str, ...] = V5_CONFIG.feature_names

    def __post_init__(self) -> None:
        if any(
            model is None
            for model in (
                self.ranker,
                self.success_classifier,
                self.severe_mae_classifier,
            )
        ):
            raise ValueError("all three V5 model heads are required")
        for name in (
            "training_rows",
            "ranker_training_rows",
            "ranker_training_dates",
            "calibration_rows",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if (
            self.model_version,
            self.feature_version,
            self.policy_version,
        ) != (
            V5_CONFIG.model_version,
            V5_CONFIG.feature_version,
            V5_CONFIG.policy_version,
        ):
            raise ValueError("V5 bundle lineage does not match the frozen model config")
        if self.feature_names != V5_CONFIG.feature_names:
            raise ValueError("V5 feature order does not match the frozen model config")


@dataclass(frozen=True)
class V5HeadPrediction:
    """Immutable outcome-free outputs for one candidate row."""

    date: Any
    symbol: str
    raw_rank_score: float
    raw_success_probability: float
    raw_severe_mae_probability: float
    calibrated_outputs: CalibratedOutputs
    model_version: str = V5_CONFIG.model_version
    feature_version: str = V5_CONFIG.feature_version
    policy_version: str = V5_CONFIG.policy_version


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise V5TrainingError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise V5TrainingError(f"{name} must be a finite number") from error
    if not math.isfinite(number):
        raise V5TrainingError(f"{name} must be a finite number")
    return number


def _probability(value: Any, name: str, *, allow_endpoints: bool) -> float:
    number = _finite_number(value, name)
    if not 0.0 <= number <= 1.0 or (not allow_endpoints and number in {0.0, 1.0}):
        raise V5TrainingError(f"{name} must be in [0, 1]")
    return number


def _project_ex_ante(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in _EX_ANTE_FEATURE_KEYS}


def build_v5_feature_matrix(
    rows: Sequence[Mapping[str, Any]],
    *,
    stability_age_sessions: int | None,
) -> tuple[tuple[float, ...], ...]:
    """Build a fixed-width immutable matrix without reading outcome fields."""
    matrix: list[tuple[float, ...]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise V5TrainingError("each V5 row must be a mapping")
        vector = tuple(
            build_v5_feature_vector(
                _project_ex_ante(row),
                stability_age_sessions=stability_age_sessions,
            )
        )
        if len(vector) != len(V5_CONFIG.feature_names):
            raise V5TrainingError("V5 feature vector has unexpected width")
        if not all(math.isfinite(float(value)) for value in vector):
            raise V5TrainingError("V5 feature vector contains a non-finite value")
        matrix.append(tuple(float(value) for value in vector))
    return tuple(matrix)


def _identity(row: Mapping[str, Any]) -> tuple[Any, str]:
    trading_date = row.get("date")
    symbol_value = row.get("symbol")
    symbol = str(symbol_value).strip() if symbol_value is not None else ""
    if trading_date is None or not symbol:
        raise V5TrainingError("each V5 row requires date and symbol")
    return trading_date, symbol


def _ordered_unique_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    role: str,
) -> tuple[Mapping[str, Any], ...]:
    seen: set[tuple[Any, str]] = set()
    keyed: list[tuple[Any, str, Mapping[str, Any]]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise V5TrainingError(f"each {role} row must be a mapping")
        trading_date, symbol = _identity(row)
        key = (trading_date, symbol)
        if key in seen:
            raise V5TrainingError(f"duplicate {role} row: {symbol} on {trading_date}")
        seen.add(key)
        keyed.append((trading_date, symbol, row))
    try:
        keyed.sort(key=lambda item: (item[0], item[1]))
    except TypeError as error:
        raise V5TrainingError(f"{role} row dates must be mutually comparable") from error
    return tuple(item[2] for item in keyed)


def _net_alpha(row: Mapping[str, Any]) -> float:
    raw_net_alpha = row.get("net_alpha_percent")
    raw_excess_return = row.get("excess_return_percent")
    net_alpha = (
        _finite_number(raw_net_alpha, "net_alpha_percent")
        if raw_net_alpha is not None
        else None
    )
    derived = (
        _finite_number(raw_excess_return, "excess_return_percent") - 1.0
        if raw_excess_return is not None
        else None
    )
    if net_alpha is not None and derived is not None and not math.isclose(
        net_alpha,
        derived,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise V5TrainingError("net alpha fields conflict")
    if net_alpha is not None:
        return net_alpha
    if derived is not None:
        return derived
    raise V5TrainingError("missing net_alpha_percent or excess_return_percent")


def _boolean_label(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    raise V5TrainingError(f"{name} must be boolean")


def _success_label(row: Mapping[str, Any]) -> bool:
    derived = _net_alpha(row) > 0.0
    if row.get("success_after_cost") is not None:
        explicit = _boolean_label(row.get("success_after_cost"), "success_after_cost")
        if explicit != derived:
            raise V5TrainingError("success_after_cost conflicts with net alpha")
    return derived


def _severe_mae_label(row: Mapping[str, Any]) -> bool:
    candidates: list[bool] = []
    for name in ("severe_mae", "severe_MAE"):
        if row.get(name) is not None:
            candidates.append(_boolean_label(row.get(name), name))
    if row.get("mae_magnitude_percent") is not None:
        candidates.append(
            _finite_number(row.get("mae_magnitude_percent"), "mae_magnitude_percent")
            >= 5.0
        )
    if row.get("max_adverse_percent") is not None:
        candidates.append(
            _finite_number(row.get("max_adverse_percent"), "max_adverse_percent")
            <= -5.0
        )
    if candidates and any(value != candidates[0] for value in candidates[1:]):
        raise V5TrainingError("severe_mae fields conflict")
    if candidates:
        return candidates[0]
    raise V5TrainingError("missing severe_mae outcome")


def _validate_binary_support(
    labels: Sequence[bool],
    *,
    head: str,
    minimum_rows: int,
    minimum_class_rows: int,
) -> None:
    if len(labels) < minimum_rows:
        raise V5TrainingError(f"{head} requires at least {minimum_rows} rows")
    positives = sum(labels)
    if positives < minimum_class_rows or len(labels) - positives < minimum_class_rows:
        raise V5TrainingError(
            f"{head} lacks frozen class support of {minimum_class_rows} per class"
        )


def _ranker_rows(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[tuple[Mapping[str, Any], ...], tuple[int, ...]]:
    grouped: dict[Any, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_identity(row)[0]].append(row)
    eligible_dates = sorted(
        trading_date
        for trading_date, values in grouped.items()
        if len(values) >= V5_TRAINING_CONFIG.minimum_rank_group_size
    )
    ordered = tuple(row for trading_date in eligible_dates for row in grouped[trading_date])
    groups = tuple(len(grouped[trading_date]) for trading_date in eligible_dates)
    if len(ordered) < V5_TRAINING_CONFIG.minimum_ranker_rows:
        raise V5TrainingError(
            f"ranker requires at least {V5_TRAINING_CONFIG.minimum_ranker_rows} eligible rows"
        )
    if len(groups) < V5_TRAINING_CONFIG.minimum_ranker_dates:
        raise V5TrainingError(
            f"ranker requires at least {V5_TRAINING_CONFIG.minimum_ranker_dates} eligible dates"
        )
    return ordered, groups


def _xgboost() -> Any:
    try:
        import xgboost as xgb
    except ImportError as error:
        raise V5TrainingError("xgboost is unavailable") from error
    return xgb


def _ndcg_relevance(targets: Sequence[float], groups: Sequence[int]) -> tuple[int, ...]:
    """Preserve each date's net-alpha ordering in bounded NDCG relevance levels."""
    result: list[int] = []
    start = 0
    for size in groups:
        values = targets[start : start + size]
        distinct = sorted(set(values))
        if len(distinct) == 1:
            relevance = {distinct[0]: 0}
        else:
            relevance = {
                value: round(4 * index / (len(distinct) - 1))
                for index, value in enumerate(distinct)
            }
        result.extend(relevance[value] for value in values)
        start += size
    if start != len(targets):
        raise V5TrainingError("ranker group sizes do not match target rows")
    return tuple(result)


def _fit_ranker(
    matrix: Sequence[Sequence[float]],
    targets: Sequence[float],
    group_sizes: Sequence[int],
) -> Any:
    xgb = _xgboost()
    data = xgb.DMatrix(
        matrix,
        label=_ndcg_relevance(targets, group_sizes),
        feature_names=list(V5_CONFIG.feature_names),
    )
    data.set_group(group_sizes)
    return xgb.train(
        dict(V5_TRAINING_CONFIG.ranker_parameters),
        data,
        num_boost_round=V5_TRAINING_CONFIG.ranker_rounds,
        verbose_eval=False,
    )


def _fit_classifier(
    matrix: Sequence[Sequence[float]],
    labels: Sequence[bool],
    *,
    head: str,
) -> Any:
    xgb = _xgboost()
    data = xgb.DMatrix(
        matrix,
        label=tuple(1 if value else 0 for value in labels),
        feature_names=list(V5_CONFIG.feature_names),
    )
    try:
        return xgb.train(
            dict(V5_TRAINING_CONFIG.classifier_parameters),
            data,
            num_boost_round=V5_TRAINING_CONFIG.classifier_rounds,
            verbose_eval=False,
        )
    except Exception as error:
        raise V5TrainingError(f"{head} classifier fitting failed") from error


def _predict_raw(model: Any, matrix: Sequence[Sequence[float]]) -> tuple[float, ...]:
    if not matrix:
        return ()
    xgb = _xgboost()
    data = xgb.DMatrix(matrix, feature_names=list(V5_CONFIG.feature_names))
    values = tuple(_finite_number(value, "model prediction") for value in model.predict(data))
    if len(values) != len(matrix):
        raise V5TrainingError("model prediction count does not match feature rows")
    return values


def _predict_probabilities(
    model: Any,
    matrix: Sequence[Sequence[float]],
) -> tuple[float, ...]:
    return tuple(
        _probability(value, "classifier probability", allow_endpoints=True)
        for value in _predict_raw(model, matrix)
    )


def fit_platt_calibrator(
    probabilities: Sequence[float],
    outcomes: Sequence[bool],
) -> PlattCalibrator:
    """Fit the independently frozen Platt map on held-out calibration labels."""
    if len(probabilities) != len(outcomes):
        raise V5TrainingError("calibration probabilities and outcomes must have equal length")
    labels = tuple(_boolean_label(value, "calibration outcome") for value in outcomes)
    _validate_binary_support(
        labels,
        head="calibration",
        minimum_rows=V5_TRAINING_CONFIG.minimum_calibration_rows,
        minimum_class_rows=V5_TRAINING_CONFIG.minimum_calibration_class_rows,
    )
    bounded = tuple(
        min(1.0 - 1e-5, max(1e-5, _probability(value, "calibration probability", allow_endpoints=True)))
        for value in probabilities
    )
    xs = tuple(math.log(value / (1.0 - value)) for value in bounded)
    ys = tuple(1.0 if value else 0.0 for value in labels)
    positives = sum(labels)
    base_rate = (positives + 1.0) / (len(labels) + 2.0)
    intercept = math.log(base_rate / (1.0 - base_rate))
    slope = 1.0

    for _ in range(V5_TRAINING_CONFIG.platt_iterations):
        intercept_gradient = 0.0
        slope_gradient = 0.0
        for value, outcome in zip(xs, ys):
            z = min(35.0, max(-35.0, intercept + slope * value))
            prediction = 1.0 / (1.0 + math.exp(-z))
            error = prediction - outcome
            intercept_gradient += error
            slope_gradient += error * value
        count = float(len(xs))
        intercept -= V5_TRAINING_CONFIG.platt_learning_rate * intercept_gradient / count
        slope -= V5_TRAINING_CONFIG.platt_learning_rate * (
            slope_gradient / count + V5_TRAINING_CONFIG.platt_l2 * (slope - 1.0)
        )

    return PlattCalibrator(intercept=intercept, slope=slope, samples=len(labels))


def fit_v5_model_bundle(
    train_rows: Sequence[Mapping[str, Any]],
    calibration_rows: Sequence[Mapping[str, Any]],
    *,
    stability_age_sessions: int | None,
) -> V5ModelBundle:
    """Fit all heads on train and both Platt maps on calibration, fail closed."""
    train = _ordered_unique_rows(train_rows, role="training")
    calibration = _ordered_unique_rows(calibration_rows, role="calibration")
    train_keys = {_identity(row) for row in train}
    calibration_keys = {_identity(row) for row in calibration}
    if train_keys & calibration_keys:
        raise V5TrainingError("training and calibration roles overlap")

    success_train = tuple(_success_label(row) for row in train)
    severe_train = tuple(_severe_mae_label(row) for row in train)
    success_calibration = tuple(_success_label(row) for row in calibration)
    severe_calibration = tuple(_severe_mae_label(row) for row in calibration)
    _validate_binary_support(
        success_train,
        head="success classifier",
        minimum_rows=V5_TRAINING_CONFIG.minimum_classifier_rows,
        minimum_class_rows=V5_TRAINING_CONFIG.minimum_classifier_class_rows,
    )
    _validate_binary_support(
        severe_train,
        head="severe_mae classifier",
        minimum_rows=V5_TRAINING_CONFIG.minimum_classifier_rows,
        minimum_class_rows=V5_TRAINING_CONFIG.minimum_classifier_class_rows,
    )
    _validate_binary_support(
        success_calibration,
        head="success calibration",
        minimum_rows=V5_TRAINING_CONFIG.minimum_calibration_rows,
        minimum_class_rows=V5_TRAINING_CONFIG.minimum_calibration_class_rows,
    )
    _validate_binary_support(
        severe_calibration,
        head="severe_mae calibration",
        minimum_rows=V5_TRAINING_CONFIG.minimum_calibration_rows,
        minimum_class_rows=V5_TRAINING_CONFIG.minimum_calibration_class_rows,
    )

    rank_rows, group_sizes = _ranker_rows(train)
    train_matrix = build_v5_feature_matrix(train, stability_age_sessions=stability_age_sessions)
    rank_matrix = build_v5_feature_matrix(
        rank_rows,
        stability_age_sessions=stability_age_sessions,
    )
    calibration_matrix = build_v5_feature_matrix(
        calibration,
        stability_age_sessions=stability_age_sessions,
    )
    rank_targets = tuple(_net_alpha(row) for row in rank_rows)

    ranker = _fit_ranker(rank_matrix, rank_targets, group_sizes)
    success_classifier = _fit_classifier(train_matrix, success_train, head="success")
    severe_classifier = _fit_classifier(train_matrix, severe_train, head="severe_mae")
    if any(model is None for model in (ranker, success_classifier, severe_classifier)):
        raise V5TrainingError("a V5 model head failed closed")

    success_calibrator = fit_platt_calibrator(
        _predict_probabilities(success_classifier, calibration_matrix),
        success_calibration,
    )
    severe_calibrator = fit_platt_calibrator(
        _predict_probabilities(severe_classifier, calibration_matrix),
        severe_calibration,
    )

    return V5ModelBundle(
        ranker=ranker,
        success_classifier=success_classifier,
        severe_mae_classifier=severe_classifier,
        success_calibrator=success_calibrator,
        severe_mae_calibrator=severe_calibrator,
        training_rows=len(train),
        ranker_training_rows=len(rank_rows),
        ranker_training_dates=len(group_sizes),
        calibration_rows=len(calibration),
    )


def within_date_average_rank_percentiles(
    rows: Sequence[Mapping[str, Any]],
    raw_scores: Sequence[float],
) -> tuple[float, ...]:
    """Convert raw scores to same-date average-rank percentiles on [0, 1]."""
    if len(rows) != len(raw_scores):
        raise V5TrainingError("rows and raw rank scores must have equal length")
    grouped: dict[Any, list[tuple[int, float]]] = defaultdict(list)
    for index, (row, score) in enumerate(zip(rows, raw_scores)):
        trading_date = row.get("date")
        if trading_date is None:
            raise V5TrainingError("each ranked row requires date")
        grouped[trading_date].append((index, _finite_number(score, "raw rank score")))

    result = [0.5] * len(rows)
    for values in grouped.values():
        ordered = sorted(values, key=lambda item: item[1])
        if len(ordered) == 1:
            continue
        positions: dict[float, list[tuple[int, int]]] = defaultdict(list)
        for position, (original_index, value) in enumerate(ordered):
            positions[value].append((original_index, position))
        denominator = len(ordered) - 1
        for tied in positions.values():
            average_position = sum(position for _, position in tied) / len(tied)
            percentile = average_position / denominator
            for original_index, _ in tied:
                result[original_index] = percentile
    return tuple(result)


def predict_v5_rows(
    bundle: V5ModelBundle,
    rows: Sequence[Mapping[str, Any]],
    *,
    stability_age_sessions: int | None,
) -> tuple[V5HeadPrediction, ...]:
    """Predict candidates using features and identity only; outcomes are untouched."""
    identities = tuple(_identity(row) for row in rows)
    if len(set(identities)) != len(identities):
        raise V5TrainingError("prediction rows contain duplicate date/symbol identities")
    matrix = build_v5_feature_matrix(rows, stability_age_sessions=stability_age_sessions)
    rank_scores = _predict_raw(bundle.ranker, matrix)
    success_raw = _predict_raw(bundle.success_classifier, matrix)
    severe_raw = _predict_raw(bundle.severe_mae_classifier, matrix)
    if not (len(rank_scores) == len(success_raw) == len(severe_raw) == len(rows)):
        raise V5TrainingError("model prediction count does not match feature rows")
    success_probabilities = tuple(
        _probability(value, "success probability", allow_endpoints=True)
        for value in success_raw
    )
    severe_probabilities = tuple(
        _probability(value, "severe_mae probability", allow_endpoints=True)
        for value in severe_raw
    )
    rank_percentiles = within_date_average_rank_percentiles(rows, rank_scores)

    return tuple(
        V5HeadPrediction(
            date=trading_date,
            symbol=symbol,
            raw_rank_score=rank_score,
            raw_success_probability=success_probability,
            raw_severe_mae_probability=severe_probability,
            calibrated_outputs=CalibratedOutputs(
                success_after_cost_probability=bundle.success_calibrator.calibrate(
                    success_probability
                ),
                within_date_alpha_signal=rank_percentile,
                severe_mae_safety_probability=1.0
                - bundle.severe_mae_calibrator.calibrate(severe_probability),
            ),
        )
        for (trading_date, symbol), rank_score, success_probability, severe_probability, rank_percentile in zip(
            identities,
            rank_scores,
            success_probabilities,
            severe_probabilities,
            rank_percentiles,
        )
    )


__all__ = [
    "PlattCalibrator",
    "V5HeadPrediction",
    "V5ModelBundle",
    "V5TrainingConfig",
    "V5TrainingError",
    "V5_TRAINING_CONFIG",
    "build_v5_feature_matrix",
    "fit_platt_calibrator",
    "fit_v5_model_bundle",
    "predict_v5_rows",
    "within_date_average_rank_percentiles",
]
