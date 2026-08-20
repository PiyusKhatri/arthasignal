from __future__ import annotations

import math
from collections import defaultdict
from statistics import mean
from typing import Any, Sequence

from src.services.quant_features import FEATURE_NAMES, feature_vector

MIN_RANK_GROUP_SIZE = 10


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def xgboost_available() -> bool:
    try:
        import xgboost  # noqa: F401

        return True
    except ImportError:
        return False


def _matrix(rows: Sequence[dict[str, Any]]) -> list[list[float]]:
    return [feature_vector(row["features"]) for row in rows]


def chronological_three_way_split(
    rows: Sequence[dict[str, Any]],
    *,
    train_fraction: float = 0.70,
    calibration_fraction: float = 0.15,
) -> dict[str, list[dict[str, Any]]]:
    """Split pooled observations by date and purge overlapping forward labels.

    Every row is expected to include `date` and `label_end_date`. A training or
    calibration row is retained only when its outcome was fully known before the
    next segment starts. This prevents the 20-day target from leaking across the
    chronological boundary.
    """
    ordered_dates = sorted({row["date"] for row in rows})
    if len(ordered_dates) < 12:
        return {"train": [], "calibration": [], "test": []}

    train_cut = max(1, min(len(ordered_dates) - 2, int(len(ordered_dates) * train_fraction)))
    calibration_cut = max(
        train_cut + 1,
        min(len(ordered_dates) - 1, int(len(ordered_dates) * (train_fraction + calibration_fraction))),
    )
    calibration_start = ordered_dates[train_cut]
    test_start = ordered_dates[calibration_cut]

    train = [
        row
        for row in rows
        if row["date"] < calibration_start and row.get("label_end_date") is not None and row["label_end_date"] < calibration_start
    ]
    calibration = [
        row
        for row in rows
        if calibration_start <= row["date"] < test_start
        and row.get("label_end_date") is not None
        and row["label_end_date"] < test_start
    ]
    test = [row for row in rows if row["date"] >= test_start]
    return {"train": train, "calibration": calibration, "test": test}


def fit_xgb_classifier(
    rows: Sequence[dict[str, Any]],
    *,
    num_boost_round: int = 220,
) -> Any | None:
    if len(rows) < 250 or not xgboost_available():
        return None

    import xgboost as xgb

    labels = [1 if row["success"] else 0 for row in rows]
    positives = sum(labels)
    if positives < 40 or positives > len(labels) - 40:
        return None

    data = xgb.DMatrix(_matrix(rows), label=labels, feature_names=list(FEATURE_NAMES))
    params = {
        "objective": "binary:logistic",
        "eval_metric": "logloss",
        "max_depth": 4,
        "eta": 0.035,
        "min_child_weight": 8,
        "subsample": 0.78,
        "colsample_bytree": 0.78,
        "lambda": 2.5,
        "alpha": 0.25,
        "max_delta_step": 1,
        "tree_method": "hist",
        "seed": 20260821,
        "nthread": 1,
    }
    return xgb.train(params, data, num_boost_round=num_boost_round, verbose_eval=False)


def predict_xgb_classifier(model: Any | None, features: dict[str, float]) -> float | None:
    if model is None or not xgboost_available():
        return None
    import xgboost as xgb

    matrix = xgb.DMatrix([feature_vector(features)], feature_names=list(FEATURE_NAMES))
    prediction = model.predict(matrix)
    return _clamp(float(prediction[0]), 0.001, 0.999) if len(prediction) else None


def predict_xgb_rows(model: Any | None, rows: Sequence[dict[str, Any]]) -> list[float]:
    if model is None or not rows or not xgboost_available():
        return []
    import xgboost as xgb

    matrix = xgb.DMatrix(_matrix(rows), feature_names=list(FEATURE_NAMES))
    return [_clamp(float(value), 0.001, 0.999) for value in model.predict(matrix)]


def fit_platt_calibrator(
    probabilities: Sequence[float],
    outcomes: Sequence[bool],
    *,
    iterations: int = 500,
    learning_rate: float = 0.035,
    l2: float = 0.02,
) -> dict[str, float] | None:
    """Fit a tiny regularized logistic calibrator on held-out predictions."""
    if len(probabilities) < 60 or len(probabilities) != len(outcomes):
        return None
    positives = sum(1 for outcome in outcomes if outcome)
    if positives < 15 or positives > len(outcomes) - 15:
        return None

    def logit(probability: float) -> float:
        p = _clamp(float(probability), 1e-5, 1.0 - 1e-5)
        return math.log(p / (1.0 - p))

    xs = [logit(value) for value in probabilities]
    ys = [1.0 if value else 0.0 for value in outcomes]
    base_rate = (positives + 1.0) / (len(ys) + 2.0)
    intercept = math.log(base_rate / (1.0 - base_rate))
    slope = 1.0

    for _ in range(iterations):
        grad_intercept = 0.0
        grad_slope = 0.0
        for x, y in zip(xs, ys):
            z = _clamp(intercept + slope * x, -35.0, 35.0)
            prediction = 1.0 / (1.0 + math.exp(-z))
            error = prediction - y
            grad_intercept += error
            grad_slope += error * x
        n = float(len(xs))
        intercept -= learning_rate * grad_intercept / n
        slope -= learning_rate * (grad_slope / n + l2 * (slope - 1.0))

    return {"intercept": intercept, "slope": slope, "samples": float(len(xs))}


def calibrate_probability(probability: float | None, calibrator: dict[str, float] | None) -> float | None:
    if probability is None:
        return None
    if calibrator is None:
        return _clamp(float(probability), 0.001, 0.999)
    p = _clamp(float(probability), 1e-5, 1.0 - 1e-5)
    logit = math.log(p / (1.0 - p))
    z = _clamp(float(calibrator["intercept"]) + float(calibrator["slope"]) * logit, -35.0, 35.0)
    return _clamp(1.0 / (1.0 + math.exp(-z)), 0.001, 0.999)


def fit_xgb_ranker(rows: Sequence[dict[str, Any]], *, num_boost_round: int = 180) -> Any | None:
    """Fit a cross-sectional LambdaMART model grouped by sufficiently broad trading dates."""
    if len(rows) < 500 or not xgboost_available():
        return None
    import xgboost as xgb

    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["date"]].append(row)
    grouped = {
        trading_date: date_rows
        for trading_date, date_rows in grouped.items()
        if len(date_rows) >= MIN_RANK_GROUP_SIZE
    }
    dates = sorted(grouped)
    ordered = [row for trading_date in dates for row in grouped[trading_date]]
    group_sizes = [len(grouped[trading_date]) for trading_date in dates]
    if len(group_sizes) < 20 or len(ordered) < 500:
        return None

    # Five relevance levels derived only from the already-observed forward excess return.
    excess = [float(row["excess_return_percent"]) for row in ordered]
    sorted_excess = sorted(excess)

    def quantile(frac: float) -> float:
        idx = max(0, min(len(sorted_excess) - 1, int(frac * (len(sorted_excess) - 1))))
        return sorted_excess[idx]

    q20, q40, q60, q80 = (quantile(0.20), quantile(0.40), quantile(0.60), quantile(0.80))

    def relevance(value: float) -> int:
        if value >= q80:
            return 4
        if value >= q60:
            return 3
        if value >= q40:
            return 2
        if value >= q20:
            return 1
        return 0

    labels = [relevance(value) for value in excess]
    data = xgb.DMatrix(_matrix(ordered), label=labels, feature_names=list(FEATURE_NAMES))
    data.set_group(group_sizes)
    params = {
        "objective": "rank:ndcg",
        "eval_metric": "ndcg@10",
        "max_depth": 4,
        "eta": 0.035,
        "min_child_weight": 10,
        "subsample": 0.80,
        "colsample_bytree": 0.80,
        "lambda": 3.0,
        "alpha": 0.30,
        "tree_method": "hist",
        "seed": 20260821,
        "nthread": 1,
    }
    return xgb.train(params, data, num_boost_round=num_boost_round, verbose_eval=False)


def predict_xgb_rank_score(model: Any | None, features: dict[str, float]) -> float | None:
    if model is None or not xgboost_available():
        return None
    import xgboost as xgb

    matrix = xgb.DMatrix([feature_vector(features)], feature_names=list(FEATURE_NAMES))
    values = model.predict(matrix)
    return float(values[0]) if len(values) else None


def precision_at_k_by_date(
    rows: Sequence[dict[str, Any]],
    scores: Sequence[float],
    *,
    k: int = 10,
) -> dict[str, Any]:
    if not rows or len(rows) != len(scores):
        return {"dates": 0, "p_at_k": None, "mean_top_k_excess_return_percent": None}

    grouped: dict[Any, list[tuple[dict[str, Any], float]]] = defaultdict(list)
    for row, score in zip(rows, scores):
        grouped[row["date"]].append((row, float(score)))

    precisions: list[float] = []
    returns: list[float] = []
    for date_rows in grouped.values():
        if len(date_rows) < k:
            continue
        ranked = sorted(date_rows, key=lambda item: item[1], reverse=True)[:k]
        precisions.append(mean(1.0 if row["success"] else 0.0 for row, _ in ranked))
        returns.append(mean(float(row["excess_return_percent"]) for row, _ in ranked))

    return {
        "dates": len(precisions),
        "p_at_k": mean(precisions) if precisions else None,
        "mean_top_k_excess_return_percent": mean(returns) if returns else None,
    }
