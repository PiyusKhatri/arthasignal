from __future__ import annotations

import math
from collections import defaultdict
from statistics import mean, median, pstdev
from typing import Any, Iterable, Sequence

FEATURE_VERSION = "nepse-quant-v1"
DEFAULT_HORIZON_DAYS = 20
ROUND_TRIP_COST_PERCENT = 0.50
ANALOG_STEP = 5
MAX_ANALOGS = 40
MIN_MODEL_ROWS = 80

FEATURE_NAMES = (
    "return_5d",
    "return_20d",
    "return_60d",
    "annualized_volatility_20d",
    "drawdown_60d",
    "turnover_ratio_20d",
    "volume_ratio_20d",
    "distance_sma50_percent",
    "relative_strength_market_20d",
    "relative_strength_sector_20d",
)

# Fixed ex-ante scales avoid normalizing a historical test row using future data.
FEATURE_SCALES = {
    "return_5d": 5.0,
    "return_20d": 10.0,
    "return_60d": 20.0,
    "annualized_volatility_20d": 30.0,
    "drawdown_60d": 15.0,
    "turnover_ratio_20d": 1.0,
    "volume_ratio_20d": 1.0,
    "distance_sma50_percent": 10.0,
    "relative_strength_market_20d": 10.0,
    "relative_strength_sector_20d": 10.0,
}

FEATURE_WEIGHTS = {
    "return_5d": 0.60,
    "return_20d": 1.10,
    "return_60d": 0.75,
    "annualized_volatility_20d": 0.70,
    "drawdown_60d": 0.80,
    "turnover_ratio_20d": 0.60,
    "volume_ratio_20d": 0.45,
    "distance_sma50_percent": 0.85,
    "relative_strength_market_20d": 1.25,
    "relative_strength_sector_20d": 0.90,
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _safe_return(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return (current / previous - 1.0) * 100.0


def _mean(values: Sequence[float]) -> float | None:
    clean = [float(v) for v in values if v is not None]
    return mean(clean) if clean else None


def _asof_value(dates: Sequence[Any], values: Sequence[float], target_date: Any) -> float | None:
    if not dates:
        return None
    lo, hi = 0, len(dates)
    while lo < hi:
        mid = (lo + hi) // 2
        if dates[mid] <= target_date:
            lo = mid + 1
        else:
            hi = mid
    idx = lo - 1
    if idx < 0:
        return None
    return float(values[idx])


def _benchmark_return(
    benchmark_dates: Sequence[Any],
    benchmark_closes: Sequence[float],
    start_date: Any,
    end_date: Any,
) -> float | None:
    start = _asof_value(benchmark_dates, benchmark_closes, start_date)
    end = _asof_value(benchmark_dates, benchmark_closes, end_date)
    return _safe_return(end, start)


def _daily_returns(prices: Sequence[float]) -> list[float]:
    result: list[float] = []
    for previous, current in zip(prices[:-1], prices[1:]):
        if previous:
            result.append(current / previous - 1.0)
    return result


def build_feature_row(
    *,
    dates: Sequence[Any],
    closes: Sequence[float],
    volumes: Sequence[float],
    turnovers: Sequence[float],
    index: int,
    market_dates: Sequence[Any],
    market_closes: Sequence[float],
    sector_dates: Sequence[Any] | None = None,
    sector_closes: Sequence[float] | None = None,
) -> dict[str, float] | None:
    if index < 60 or index >= len(closes):
        return None

    current = float(closes[index])
    if current <= 0:
        return None

    ret_5 = _safe_return(current, float(closes[index - 5]))
    ret_20 = _safe_return(current, float(closes[index - 20]))
    ret_60 = _safe_return(current, float(closes[index - 60]))
    if ret_5 is None or ret_20 is None or ret_60 is None:
        return None

    trailing_prices = [float(v) for v in closes[max(0, index - 20) : index + 1] if float(v) > 0]
    daily = _daily_returns(trailing_prices)
    annualized_vol = pstdev(daily) * math.sqrt(252.0) * 100.0 if len(daily) >= 2 else 0.0

    window_60 = [float(v) for v in closes[index - 59 : index + 1] if float(v) > 0]
    peak_60 = max(window_60) if window_60 else current
    drawdown_60 = (current / peak_60 - 1.0) * 100.0 if peak_60 else 0.0

    trailing_turnover = [float(v or 0.0) for v in turnovers[max(0, index - 20) : index]]
    avg_turnover = _mean(trailing_turnover)
    turnover_ratio = float(turnovers[index] or 0.0) / avg_turnover if avg_turnover and avg_turnover > 0 else 1.0

    trailing_volume = [float(v or 0.0) for v in volumes[max(0, index - 20) : index]]
    avg_volume = _mean(trailing_volume)
    volume_ratio = float(volumes[index] or 0.0) / avg_volume if avg_volume and avg_volume > 0 else 1.0

    sma50 = _mean([float(v) for v in closes[max(0, index - 49) : index + 1]])
    distance_sma50 = _safe_return(current, sma50) if sma50 else 0.0

    market_20 = _benchmark_return(market_dates, market_closes, dates[index - 20], dates[index])
    sector_20 = None
    if sector_dates and sector_closes:
        sector_20 = _benchmark_return(sector_dates, sector_closes, dates[index - 20], dates[index])

    return {
        "return_5d": ret_5,
        "return_20d": ret_20,
        "return_60d": ret_60,
        "annualized_volatility_20d": annualized_vol,
        "drawdown_60d": drawdown_60,
        "turnover_ratio_20d": _clamp(turnover_ratio, 0.0, 5.0),
        "volume_ratio_20d": _clamp(volume_ratio, 0.0, 5.0),
        "distance_sma50_percent": distance_sma50 or 0.0,
        "relative_strength_market_20d": ret_20 - (market_20 or 0.0),
        "relative_strength_sector_20d": ret_20 - (sector_20 if sector_20 is not None else (market_20 or 0.0)),
    }


def build_labeled_feature_rows(
    *,
    dates: Sequence[Any],
    closes: Sequence[float],
    volumes: Sequence[float],
    turnovers: Sequence[float],
    market_dates: Sequence[Any],
    market_closes: Sequence[float],
    sector_dates: Sequence[Any] | None = None,
    sector_closes: Sequence[float] | None = None,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    step: int = ANALOG_STEP,
    end_index: int | None = None,
) -> list[dict[str, Any]]:
    if len(closes) <= 80:
        return []

    last_known = len(closes) - horizon_days - 1
    if end_index is not None:
        last_known = min(last_known, end_index)
    start = 60
    rows: list[dict[str, Any]] = []

    for idx in range(start, last_known + 1, max(1, step)):
        features = build_feature_row(
            dates=dates,
            closes=closes,
            volumes=volumes,
            turnovers=turnovers,
            index=idx,
            market_dates=market_dates,
            market_closes=market_closes,
            sector_dates=sector_dates,
            sector_closes=sector_closes,
        )
        if not features:
            continue

        future_idx = idx + horizon_days
        stock_return = _safe_return(float(closes[future_idx]), float(closes[idx]))
        market_return = _benchmark_return(market_dates, market_closes, dates[idx], dates[future_idx])
        if stock_return is None or market_return is None:
            continue

        excess_return = stock_return - market_return
        path_returns = [
            _safe_return(float(closes[j]), float(closes[idx])) or 0.0
            for j in range(idx + 1, future_idx + 1)
        ]
        max_adverse = min(path_returns) if path_returns else 0.0
        max_favorable = max(path_returns) if path_returns else 0.0

        rows.append(
            {
                "index": idx,
                "date": dates[idx],
                "features": features,
                "stock_return_percent": stock_return,
                "market_return_percent": market_return,
                "excess_return_percent": excess_return,
                "success": excess_return > ROUND_TRIP_COST_PERCENT,
                "max_adverse_percent": max_adverse,
                "max_favorable_percent": max_favorable,
            }
        )

    return rows


def feature_vector(features: dict[str, float]) -> list[float]:
    vector = []
    for name in FEATURE_NAMES:
        scale = FEATURE_SCALES[name]
        vector.append(_clamp(float(features.get(name, 0.0)) / scale, -5.0, 5.0))
    return vector


def feature_distance(a: dict[str, float], b: dict[str, float]) -> float:
    total = 0.0
    weight_total = 0.0
    for name in FEATURE_NAMES:
        scale = FEATURE_SCALES[name]
        weight = FEATURE_WEIGHTS[name]
        delta = (float(a.get(name, 0.0)) - float(b.get(name, 0.0))) / scale
        total += weight * delta * delta
        weight_total += weight
    return math.sqrt(total / weight_total) if weight_total else 0.0


def analyze_historical_analogs(
    current_features: dict[str, float],
    labeled_rows: Sequence[dict[str, Any]],
    max_analogs: int = MAX_ANALOGS,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for row in labeled_rows:
        distance = feature_distance(current_features, row["features"])
        similarity = math.exp(-distance)
        candidates.append({**row, "distance": distance, "similarity": similarity})

    candidates.sort(key=lambda row: (row["distance"], row["date"]))
    analogs = candidates[:max_analogs]
    if not analogs:
        return {
            "probability_outperform_after_cost": None,
            "expected_excess_return_percent": None,
            "median_excess_return_percent": None,
            "downside_25th_percent": None,
            "mean_max_adverse_percent": None,
            "mean_max_favorable_percent": None,
            "confidence_score": 0,
            "effective_sample_size": 0.0,
            "analogs": [],
        }

    weights = [max(float(row["similarity"]), 0.05) for row in analogs]
    weight_sum = sum(weights)
    successes = sum(w for w, row in zip(weights, analogs) if row["success"])

    # Empirical Bayes shrinkage towards an uninformative 50% prior.
    prior_strength = 8.0
    probability = (successes + 0.5 * prior_strength) / (weight_sum + prior_strength)
    expected_excess = sum(w * float(row["excess_return_percent"]) for w, row in zip(weights, analogs)) / weight_sum
    sorted_excess = sorted(float(row["excess_return_percent"]) for row in analogs)
    q25_index = max(0, min(len(sorted_excess) - 1, int(0.25 * (len(sorted_excess) - 1))))

    sum_sq = sum(w * w for w in weights)
    effective_n = (weight_sum * weight_sum / sum_sq) if sum_sq else 0.0
    avg_similarity = sum(weights) / len(weights)
    confidence = round(
        _clamp(
            15.0 + min(effective_n, 30.0) * 1.5 + avg_similarity * 35.0,
            0.0,
            92.0,
        )
    )

    return {
        "probability_outperform_after_cost": probability,
        "expected_excess_return_percent": expected_excess,
        "median_excess_return_percent": median(sorted_excess),
        "downside_25th_percent": sorted_excess[q25_index],
        "mean_max_adverse_percent": mean(float(row["max_adverse_percent"]) for row in analogs),
        "mean_max_favorable_percent": mean(float(row["max_favorable_percent"]) for row in analogs),
        "confidence_score": confidence,
        "effective_sample_size": effective_n,
        "average_similarity": avg_similarity,
        "analogs": [
            {
                "date": row["date"].isoformat() if hasattr(row["date"], "isoformat") else str(row["date"]),
                "similarity": row["similarity"],
                "stock_return_percent": row["stock_return_percent"],
                "market_return_percent": row["market_return_percent"],
                "excess_return_percent": row["excess_return_percent"],
                "max_adverse_percent": row["max_adverse_percent"],
                "max_favorable_percent": row["max_favorable_percent"],
            }
            for row in analogs[:8]
        ],
    }


def _sigmoid(value: float) -> float:
    value = _clamp(value, -35.0, 35.0)
    return 1.0 / (1.0 + math.exp(-value))


def fit_ridge_logistic(
    rows: Sequence[dict[str, Any]],
    *,
    iterations: int = 220,
    learning_rate: float = 0.045,
    l2: float = 0.08,
) -> dict[str, Any] | None:
    if len(rows) < MIN_MODEL_ROWS:
        return None

    labels = [1.0 if row["success"] else 0.0 for row in rows]
    positives = sum(labels)
    if positives < 10 or positives > len(labels) - 10:
        return None

    matrix = [feature_vector(row["features"]) for row in rows]
    width = len(FEATURE_NAMES)
    weights = [0.0] * width
    base_rate = (positives + 1.0) / (len(labels) + 2.0)
    bias = math.log(base_rate / (1.0 - base_rate))

    # Recency gets a modest, pre-defined weight while all observations remain represented.
    sample_weights = [0.65 + 0.35 * (i + 1) / len(rows) for i in range(len(rows))]
    normalization = sum(sample_weights)

    for _ in range(iterations):
        grad_w = [0.0] * width
        grad_b = 0.0
        for x, y, sample_weight in zip(matrix, labels, sample_weights):
            prediction = _sigmoid(bias + sum(w * value for w, value in zip(weights, x)))
            error = (prediction - y) * sample_weight
            grad_b += error
            for j in range(width):
                grad_w[j] += error * x[j]

        bias -= learning_rate * grad_b / normalization
        for j in range(width):
            gradient = grad_w[j] / normalization + l2 * weights[j]
            weights[j] -= learning_rate * gradient

    return {
        "model": "ridge_logistic",
        "feature_version": FEATURE_VERSION,
        "training_rows": len(rows),
        "base_rate": base_rate,
        "bias": bias,
        "weights": {name: weights[i] for i, name in enumerate(FEATURE_NAMES)},
    }


def predict_ridge_logistic(model: dict[str, Any] | None, features: dict[str, float]) -> float | None:
    if not model:
        return None
    x = feature_vector(features)
    weights = model.get("weights", {})
    linear = float(model.get("bias", 0.0))
    linear += sum(float(weights.get(name, 0.0)) * x[i] for i, name in enumerate(FEATURE_NAMES))
    return _sigmoid(linear)


def build_probability_ensemble(
    *,
    analog_result: dict[str, Any],
    logistic_probability: float | None,
    training_rows: int,
) -> dict[str, Any]:
    analog_probability = analog_result.get("probability_outperform_after_cost")
    analog_confidence = int(analog_result.get("confidence_score") or 0)

    if analog_probability is None and logistic_probability is None:
        return {
            "probability_outperform_after_cost": None,
            "confidence_score": 0,
            "components": {},
            "calibration_status": "insufficient_history",
        }

    if analog_probability is None:
        probability = float(logistic_probability)
        disagreement = 0.0
    elif logistic_probability is None:
        probability = float(analog_probability)
        disagreement = 0.0
    else:
        probability = 0.60 * float(analog_probability) + 0.40 * float(logistic_probability)
        disagreement = abs(float(analog_probability) - float(logistic_probability))

    model_confidence = min(90, round(30 + math.sqrt(max(training_rows, 0)) * 2.0)) if training_rows else 0
    if analog_probability is None:
        confidence = model_confidence
    elif logistic_probability is None:
        confidence = analog_confidence
    else:
        confidence = min(analog_confidence, model_confidence)
        confidence = round(_clamp(confidence - disagreement * 80.0, 0.0, 92.0))

    return {
        "probability_outperform_after_cost": _clamp(probability, 0.02, 0.98),
        "confidence_score": confidence,
        "components": {
            "historical_analog_probability": analog_probability,
            "ridge_logistic_probability": logistic_probability,
            "model_disagreement": disagreement,
        },
        "calibration_status": "shadow_validation_required",
    }


def calibration_bins(predictions: Sequence[float], outcomes: Sequence[bool]) -> list[dict[str, Any]]:
    bins: dict[int, list[tuple[float, bool]]] = defaultdict(list)
    for prediction, outcome in zip(predictions, outcomes):
        bucket = min(9, max(0, int(float(prediction) * 10)))
        bins[bucket].append((float(prediction), bool(outcome)))

    result = []
    for bucket in sorted(bins):
        values = bins[bucket]
        result.append(
            {
                "range": f"{bucket / 10:.1f}-{(bucket + 1) / 10:.1f}",
                "count": len(values),
                "mean_predicted_probability": mean(v[0] for v in values),
                "observed_success_rate": mean(1.0 if v[1] else 0.0 for v in values),
            }
        )
    return result


def evaluate_predictions(
    predictions: Sequence[float],
    outcomes: Sequence[bool],
    excess_returns: Sequence[float] | None = None,
    *,
    high_confidence_threshold: float = 0.65,
) -> dict[str, Any]:
    if not predictions:
        return {
            "samples": 0,
            "brier_score": None,
            "high_confidence_samples": 0,
            "high_confidence_precision": None,
            "coverage": 0.0,
            "calibration_bins": [],
        }

    brier = mean((float(p) - (1.0 if y else 0.0)) ** 2 for p, y in zip(predictions, outcomes))
    high_indices = [i for i, p in enumerate(predictions) if float(p) >= high_confidence_threshold]
    high_precision = None
    if high_indices:
        high_precision = mean(1.0 if outcomes[i] else 0.0 for i in high_indices)

    result = {
        "samples": len(predictions),
        "brier_score": brier,
        "high_confidence_threshold": high_confidence_threshold,
        "high_confidence_samples": len(high_indices),
        "high_confidence_precision": high_precision,
        "coverage": len(high_indices) / len(predictions),
        "calibration_bins": calibration_bins(predictions, outcomes),
    }
    if excess_returns is not None and high_indices:
        result["high_confidence_mean_excess_return_percent"] = mean(float(excess_returns[i]) for i in high_indices)
    else:
        result["high_confidence_mean_excess_return_percent"] = None
    return result
