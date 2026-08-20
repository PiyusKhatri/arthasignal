from __future__ import annotations

import math
import random
from collections import defaultdict
from statistics import mean, median
from typing import Any, Sequence

from src.services.quant_decision_policy import consensus_market_regime, consensus_sector_regime
from src.services.quant_features import FEATURE_NAMES, FEATURE_SCALES, feature_vector

V4_POLICY_VERSION = "2026-08-21-residual-alpha-v1"
V4_EXECUTION_HURDLE_PERCENT = 1.00
V4_CANDIDATE_POOL_FRACTION = 0.25
V4_CANDIDATE_POOL_MIN = 8
V4_CANDIDATE_POOL_MAX = 30
V4_MIN_RESIDUAL_PROBABILITY = 0.55
V4_MIN_PREDICTED_RESIDUAL_PERCENT = 0.0
V4_MAX_PREDICTED_MAE_PERCENT = 10.0
V4_MIN_PREDICTED_PAYOFF_RATIO = 0.80
V4_BOOTSTRAP_ITERATIONS = 1000
V4_BOOTSTRAP_SEED = 20260821
V4_FINAL_CAPACITY = {
    "strong_bull": 5,
    "bull": 5,
    "recovery": 5,
    "sideways": 3,
    "distribution": 0,
    "bear": 0,
    "high_stress": 0,
}

V4_META_FEATURE_NAMES = tuple(FEATURE_NAMES) + (
    "baseline_score",
    "baseline_percentile",
    "baseline_expected_excess",
    "market_return_20d",
    "market_return_60d",
    "market_drawdown_252d",
    "market_volatility_60d",
    "sector_relative_strength_20d",
    "sector_leading",
    "high_liquidity",
    "horizon_slippage_sessions",
)


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


def _normalized_feature(row: dict[str, Any], name: str) -> float:
    raw = float(row.get("features", {}).get(name, 0.0))
    scale = float(FEATURE_SCALES[name])
    return _clamp(raw / scale, -5.0, 5.0) if scale > 0 else raw


def fixed_multifactor_score(row: dict[str, Any]) -> float:
    """Transparent factor baseline. Coefficients are fixed and never fit on outcomes."""
    return (
        1.00 * _normalized_feature(row, "relative_strength_market_20d")
        + 0.75 * _normalized_feature(row, "relative_strength_sector_20d")
        + 0.40 * _normalized_feature(row, "return_60d")
        + 0.30 * _normalized_feature(row, "distance_sma50_percent")
        + 0.20 * _normalized_feature(row, "turnover_ratio_20d")
        - 0.20 * _normalized_feature(row, "annualized_volatility_20d")
    )


def _rank_bucket(rank: int) -> str:
    if rank <= 5:
        return "top_5"
    if rank <= 10:
        return "rank_6_10"
    if rank <= 20:
        return "rank_11_20"
    return "rank_21_plus"


def build_baseline_candidate_pool(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create a feature-only candidate universe before any V4 ML model is consulted."""
    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["date"]].append(row)

    candidates: list[dict[str, Any]] = []
    for trading_date in sorted(grouped):
        date_rows = grouped[trading_date]
        market_regime = consensus_market_regime(date_rows)
        if V4_FINAL_CAPACITY.get(market_regime, 0) <= 0:
            continue

        sector_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in date_rows:
            sector_groups[str(row.get("sector") or "unknown")].append(row)
        sector_labels = {
            sector: consensus_sector_regime(sector_rows)
            for sector, sector_rows in sector_groups.items()
        }

        eligible: list[dict[str, Any]] = []
        for row in date_rows:
            sector = str(row.get("sector") or "unknown")
            sector_regime = sector_labels[sector]
            if sector_regime == "lagging":
                continue
            if market_regime == "sideways" and sector_regime != "leading":
                continue
            eligible.append(row)

        if not eligible:
            continue
        ranked = sorted(eligible, key=fixed_multifactor_score, reverse=True)
        requested = max(
            V4_CANDIDATE_POOL_MIN,
            int(math.ceil(len(ranked) * V4_CANDIDATE_POOL_FRACTION)),
        )
        pool_size = min(len(ranked), V4_CANDIDATE_POOL_MAX, requested)
        denominator = max(1, len(ranked) - 1)
        for position, row in enumerate(ranked[:pool_size], start=1):
            sector = str(row.get("sector") or "unknown")
            candidates.append(
                {
                    **row,
                    "v4_market_regime": market_regime,
                    "v4_sector_regime": sector_labels[sector],
                    "baseline_score": fixed_multifactor_score(row),
                    "baseline_rank": position,
                    "baseline_percentile": 1.0 - (position - 1) / denominator,
                    "baseline_rank_bucket": _rank_bucket(position),
                }
            )
    return candidates


def fit_baseline_expectation(candidates: Sequence[dict[str, Any]], *, min_cell_rows: int = 20) -> dict[str, Any]:
    """Fit a transparent conditional return table on training candidates only."""
    detailed: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    market_bucket: dict[tuple[str, str], list[float]] = defaultdict(list)
    bucket_only: dict[str, list[float]] = defaultdict(list)
    all_values: list[float] = []

    for row in candidates:
        value = float(row["excess_return_percent"])
        market = str(row["v4_market_regime"])
        sector = str(row["v4_sector_regime"])
        bucket = str(row["baseline_rank_bucket"])
        detailed[(market, sector, bucket)].append(value)
        market_bucket[(market, bucket)].append(value)
        bucket_only[bucket].append(value)
        all_values.append(value)

    def reduced(source: dict[Any, list[float]]) -> dict[Any, float]:
        return {
            key: float(median(values))
            for key, values in source.items()
            if len(values) >= min_cell_rows
        }

    return {
        "detailed": reduced(detailed),
        "market_bucket": reduced(market_bucket),
        "bucket_only": reduced(bucket_only),
        "overall": float(median(all_values)) if all_values else 0.0,
        "training_candidates": len(candidates),
        "min_cell_rows": min_cell_rows,
    }


def predict_baseline_expectation(model: dict[str, Any], candidate: dict[str, Any]) -> float:
    market = str(candidate["v4_market_regime"])
    sector = str(candidate["v4_sector_regime"])
    bucket = str(candidate["baseline_rank_bucket"])
    detailed = model.get("detailed", {})
    market_bucket = model.get("market_bucket", {})
    bucket_only = model.get("bucket_only", {})
    if (market, sector, bucket) in detailed:
        return float(detailed[(market, sector, bucket)])
    if (market, bucket) in market_bucket:
        return float(market_bucket[(market, bucket)])
    if bucket in bucket_only:
        return float(bucket_only[bucket])
    return float(model.get("overall") or 0.0)


def attach_residual_targets(
    candidates: Sequence[dict[str, Any]],
    baseline_model: dict[str, Any],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for candidate in candidates:
        expected = predict_baseline_expectation(baseline_model, candidate)
        residual = float(candidate["excess_return_percent"]) - expected
        result.append(
            {
                **candidate,
                "baseline_expected_excess_percent": expected,
                "residual_alpha_percent": residual,
                "residual_positive": residual > 0.0,
                "mae_magnitude_percent": max(0.0, -float(candidate.get("max_adverse_percent") or 0.0)),
                "mfe_percent": max(0.0, float(candidate.get("max_favorable_percent") or 0.0)),
            }
        )
    return result


def v4_feature_vector(row: dict[str, Any]) -> list[float]:
    context = row.get("regime_context", {})
    base = feature_vector(row.get("features", {}))
    return base + [
        _clamp(float(row.get("baseline_score") or 0.0) / 3.0, -5.0, 5.0),
        _clamp(float(row.get("baseline_percentile") or 0.0), 0.0, 1.0),
        _clamp(float(row.get("baseline_expected_excess_percent") or 0.0) / 5.0, -5.0, 5.0),
        _clamp(float(context.get("market_return_20d_percent") or 0.0) / 10.0, -5.0, 5.0),
        _clamp(float(context.get("market_return_60d_percent") or 0.0) / 20.0, -5.0, 5.0),
        _clamp(float(context.get("market_drawdown_252d_percent") or 0.0) / 15.0, -5.0, 5.0),
        _clamp(float(context.get("market_annualized_volatility_60d_percent") or 0.0) / 30.0, 0.0, 5.0),
        _clamp(float(context.get("sector_relative_strength_20d_percent") or 0.0) / 10.0, -5.0, 5.0),
        1.0 if row.get("v4_sector_regime") == "leading" else 0.0,
        1.0 if row.get("liquidity_bucket") == "high" else 0.0,
        _clamp(float(row.get("horizon_slippage_sessions") or 0.0) / 3.0, 0.0, 2.0),
    ]


def xgboost_available() -> bool:
    try:
        import xgboost  # noqa: F401
        return True
    except ImportError:
        return False


def _v4_matrix(rows: Sequence[dict[str, Any]]) -> list[list[float]]:
    return [v4_feature_vector(row) for row in rows]


def fit_v4_regressor(
    rows: Sequence[dict[str, Any]],
    *,
    target_key: str,
    clip_low: float,
    clip_high: float,
    num_boost_round: int = 180,
) -> Any | None:
    if len(rows) < 250 or not xgboost_available():
        return None
    import xgboost as xgb

    labels = [_clamp(float(row[target_key]), clip_low, clip_high) for row in rows]
    data = xgb.DMatrix(_v4_matrix(rows), label=labels, feature_names=list(V4_META_FEATURE_NAMES))
    params = {
        "objective": "reg:squarederror",
        "eval_metric": "rmse",
        "max_depth": 3,
        "eta": 0.035,
        "min_child_weight": 10,
        "subsample": 0.76,
        "colsample_bytree": 0.76,
        "lambda": 3.5,
        "alpha": 0.50,
        "tree_method": "hist",
        "seed": 20260821,
        "nthread": 1,
    }
    return xgb.train(params, data, num_boost_round=num_boost_round, verbose_eval=False)


def predict_v4_regressor(model: Any | None, rows: Sequence[dict[str, Any]]) -> list[float]:
    if model is None or not rows or not xgboost_available():
        return []
    import xgboost as xgb

    matrix = xgb.DMatrix(_v4_matrix(rows), feature_names=list(V4_META_FEATURE_NAMES))
    return [float(value) for value in model.predict(matrix)]


def fit_v4_classifier(
    rows: Sequence[dict[str, Any]],
    *,
    target_key: str = "residual_positive",
    num_boost_round: int = 180,
) -> Any | None:
    if len(rows) < 250 or not xgboost_available():
        return None
    import xgboost as xgb

    labels = [1 if bool(row[target_key]) else 0 for row in rows]
    positives = sum(labels)
    if positives < 40 or positives > len(labels) - 40:
        return None
    data = xgb.DMatrix(_v4_matrix(rows), label=labels, feature_names=list(V4_META_FEATURE_NAMES))
    params = {
        "objective": "binary:logistic",
        "eval_metric": "logloss",
        "max_depth": 3,
        "eta": 0.035,
        "min_child_weight": 10,
        "subsample": 0.76,
        "colsample_bytree": 0.76,
        "lambda": 3.5,
        "alpha": 0.50,
        "max_delta_step": 1,
        "tree_method": "hist",
        "seed": 20260821,
        "nthread": 1,
    }
    return xgb.train(params, data, num_boost_round=num_boost_round, verbose_eval=False)


def predict_v4_classifier(model: Any | None, rows: Sequence[dict[str, Any]]) -> list[float]:
    if model is None or not rows or not xgboost_available():
        return []
    import xgboost as xgb

    matrix = xgb.DMatrix(_v4_matrix(rows), feature_names=list(V4_META_FEATURE_NAMES))
    return [_clamp(float(value), 0.001, 0.999) for value in model.predict(matrix)]


def fit_affine_calibrator(
    predictions: Sequence[float],
    actuals: Sequence[float],
    *,
    shrinkage: float = 0.25,
) -> dict[str, float] | None:
    if len(predictions) < 40 or len(predictions) != len(actuals):
        return None
    xs = [float(value) for value in predictions]
    ys = [float(value) for value in actuals]
    mean_x = mean(xs)
    mean_y = mean(ys)
    variance = mean((value - mean_x) ** 2 for value in xs)
    if variance <= 1e-10:
        return {"intercept": mean_y, "slope": 0.0, "samples": float(len(xs))}
    covariance = mean((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = covariance / (variance * (1.0 + max(0.0, shrinkage)))
    return {
        "intercept": mean_y - slope * mean_x,
        "slope": slope,
        "samples": float(len(xs)),
    }


def apply_affine_calibrator(value: float, calibrator: dict[str, float] | None) -> float:
    if calibrator is None:
        return float(value)
    return float(calibrator["intercept"]) + float(calibrator["slope"]) * float(value)


def build_v4_predictions(
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
        predicted_mae = max(0.0, float(mae))
        predicted_mfe = max(0.0, float(mfe))
        predicted_residual = float(residual)
        expected_excess = float(row.get("baseline_expected_excess_percent") or 0.0) + predicted_residual
        payoff_ratio = predicted_mfe / max(1.0, predicted_mae)
        result.append(
            {
                "row": row,
                "predicted_residual_alpha_percent": predicted_residual,
                "probability_positive_residual": _clamp(float(probability), 0.001, 0.999),
                "predicted_mae_percent": predicted_mae,
                "predicted_mfe_percent": predicted_mfe,
                "predicted_payoff_ratio": payoff_ratio,
                "expected_excess_return_percent": expected_excess,
                "market_regime": row["v4_market_regime"],
                "sector_regime": row["v4_sector_regime"],
            }
        )
    return result


def _is_v4_qualified(candidate: dict[str, Any]) -> bool:
    if V4_FINAL_CAPACITY.get(str(candidate["market_regime"]), 0) <= 0:
        return False
    if float(candidate["probability_positive_residual"]) < V4_MIN_RESIDUAL_PROBABILITY:
        return False
    if float(candidate["predicted_residual_alpha_percent"]) <= V4_MIN_PREDICTED_RESIDUAL_PERCENT:
        return False
    if float(candidate["expected_excess_return_percent"]) <= V4_EXECUTION_HURDLE_PERCENT:
        return False
    if float(candidate["predicted_mae_percent"]) > V4_MAX_PREDICTED_MAE_PERCENT:
        return False
    if float(candidate["predicted_payoff_ratio"]) < V4_MIN_PREDICTED_PAYOFF_RATIO:
        return False
    return True


def select_v4_setups(candidates: Sequence[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate["row"]["date"]].append(candidate)

    selected: list[dict[str, Any]] = []
    days: list[dict[str, Any]] = []
    for trading_date in sorted(grouped):
        date_candidates = grouped[trading_date]
        market = str(date_candidates[0]["market_regime"])
        capacity = V4_FINAL_CAPACITY.get(market, 0)
        qualified = [candidate for candidate in date_candidates if _is_v4_qualified(candidate)]
        chosen = sorted(
            qualified,
            key=lambda candidate: (
                float(candidate["predicted_residual_alpha_percent"]),
                float(candidate["predicted_payoff_ratio"]),
                float(candidate["probability_positive_residual"]),
            ),
            reverse=True,
        )[:capacity]
        selected.extend(chosen)
        days.append(
            {
                "date": trading_date,
                "market_regime": market,
                "candidate_pool": len(date_candidates),
                "qualified": len(qualified),
                "capacity": capacity,
                "selected": len(chosen),
            }
        )
    return {"selected": selected, "days": days}


def matched_baseline_selection(
    candidate_rows: Sequence[dict[str, Any]],
    selected_counts: dict[Any, int],
) -> dict[str, Any]:
    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in candidate_rows:
        grouped[row["date"]].append(row)

    selected: list[dict[str, Any]] = []
    days: list[dict[str, Any]] = []
    for trading_date in sorted(grouped):
        count = max(0, int(selected_counts.get(trading_date, 0)))
        ranked = sorted(grouped[trading_date], key=lambda row: float(row["baseline_score"]), reverse=True)
        chosen = ranked[:count]
        selected.extend({"row": row, "market_regime": row["v4_market_regime"]} for row in chosen)
        days.append(
            {
                "date": trading_date,
                "market_regime": ranked[0]["v4_market_regime"] if ranked else "high_stress",
                "capacity": count,
                "selected": len(chosen),
            }
        )
    return {"selected": selected, "days": days}


def selection_counts(selection: dict[str, Any]) -> dict[Any, int]:
    return {day["date"]: int(day.get("selected") or 0) for day in selection.get("days", [])}


def evaluate_selection(selection: dict[str, Any]) -> dict[str, Any]:
    candidates = selection.get("selected", [])
    rows = [candidate["row"] for candidate in candidates]
    net = [float(row["excess_return_percent"]) - V4_EXECUTION_HURDLE_PERCENT for row in rows]
    adverse = [max(0.0, -float(row.get("max_adverse_percent") or 0.0)) for row in rows]
    favorable = [max(0.0, float(row.get("max_favorable_percent") or 0.0)) for row in rows]
    active_dates = {row["date"] for row in rows}
    severe = [value >= 5.0 for value in adverse]
    return {
        "selected_rows": len(rows),
        "active_dates": len(active_dates),
        "precision_after_1pct": mean(1.0 if value > 0.0 else 0.0 for value in net) if net else None,
        "mean_net_excess_percent": mean(net) if net else None,
        "median_net_excess_percent": median(net) if net else None,
        "severe_drawdown_rate": mean(1.0 if value else 0.0 for value in severe) if severe else None,
        "mean_mae_percent": mean(adverse) if adverse else None,
        "mean_mfe_percent": mean(favorable) if favorable else None,
        "mean_realized_payoff_ratio": (
            mean(mfe / max(1.0, mae) for mfe, mae in zip(favorable, adverse))
            if adverse
            else None
        ),
    }


def compare_same_breadth(
    v4_selection: dict[str, Any],
    baseline_selection: dict[str, Any],
) -> dict[str, Any]:
    def grouped(selection: dict[str, Any]) -> dict[Any, list[dict[str, Any]]]:
        result: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        for candidate in selection.get("selected", []):
            result[candidate["row"]["date"]].append(candidate["row"])
        return result

    v4 = grouped(v4_selection)
    baseline = grouped(baseline_selection)
    increments: list[float] = []
    date_rows: list[dict[str, Any]] = []
    for trading_date in sorted(set(v4) & set(baseline)):
        if len(v4[trading_date]) != len(baseline[trading_date]) or not v4[trading_date]:
            continue
        v4_net = mean(float(row["excess_return_percent"]) - V4_EXECUTION_HURDLE_PERCENT for row in v4[trading_date])
        baseline_net = mean(float(row["excess_return_percent"]) - V4_EXECUTION_HURDLE_PERCENT for row in baseline[trading_date])
        increment = v4_net - baseline_net
        increments.append(increment)
        date_rows.append(
            {
                "date": trading_date,
                "breadth": len(v4[trading_date]),
                "v4_net_excess_percent": v4_net,
                "baseline_net_excess_percent": baseline_net,
                "incremental_net_excess_percent": increment,
            }
        )

    rng = random.Random(V4_BOOTSTRAP_SEED)
    bootstrap: list[float] = []
    if increments:
        for _ in range(V4_BOOTSTRAP_ITERATIONS):
            sample = [increments[rng.randrange(len(increments))] for _ in range(len(increments))]
            bootstrap.append(mean(sample))

    return {
        "matched_dates": len(increments),
        "mean_incremental_net_excess_percent": mean(increments) if increments else None,
        "median_incremental_net_excess_percent": median(increments) if increments else None,
        "positive_incremental_date_rate": mean(1.0 if value > 0.0 else 0.0 for value in increments) if increments else None,
        "incremental_bootstrap_95": {
            "low": _percentile(bootstrap, 0.025),
            "high": _percentile(bootstrap, 0.975),
        },
        "recent_dates": [
            {
                **row,
                "date": row["date"].isoformat() if hasattr(row["date"], "isoformat") else str(row["date"]),
            }
            for row in date_rows[-10:]
        ],
    }


def non_overlapping_incremental_portfolio(
    v4_selection: dict[str, Any],
    baseline_selection: dict[str, Any],
) -> dict[str, Any]:
    def grouped(selection: dict[str, Any]) -> dict[Any, list[dict[str, Any]]]:
        result: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        for candidate in selection.get("selected", []):
            result[candidate["row"]["date"]].append(candidate["row"])
        return result

    v4 = grouped(v4_selection)
    baseline = grouped(baseline_selection)
    cohorts: list[dict[str, Any]] = []
    active_until = None
    for trading_date in sorted(set(v4) & set(baseline)):
        if active_until is not None and trading_date <= active_until:
            continue
        v4_rows = v4[trading_date]
        baseline_rows = baseline[trading_date]
        if not v4_rows or len(v4_rows) != len(baseline_rows):
            continue
        end_dates = [row.get("label_end_date") for row in v4_rows if row.get("label_end_date") is not None]
        if not end_dates:
            continue
        active_until = max(end_dates)
        v4_net_excess = mean(float(row["excess_return_percent"]) - V4_EXECUTION_HURDLE_PERCENT for row in v4_rows)
        baseline_net_excess = mean(float(row["excess_return_percent"]) - V4_EXECUTION_HURDLE_PERCENT for row in baseline_rows)
        v4_stock_return = mean(float(row["stock_return_percent"]) - V4_EXECUTION_HURDLE_PERCENT for row in v4_rows)
        baseline_stock_return = mean(float(row["stock_return_percent"]) - V4_EXECUTION_HURDLE_PERCENT for row in baseline_rows)
        market_return = mean(float(row["market_return_percent"]) for row in v4_rows)
        cohorts.append(
            {
                "date": trading_date,
                "end_date": active_until,
                "breadth": len(v4_rows),
                "v4_net_excess_percent": v4_net_excess,
                "baseline_net_excess_percent": baseline_net_excess,
                "incremental_percent": v4_net_excess - baseline_net_excess,
                "v4_stock_return_percent": v4_stock_return,
                "baseline_stock_return_percent": baseline_stock_return,
                "market_return_percent": market_return,
            }
        )

    increments = [float(row["incremental_percent"]) for row in cohorts]
    rng = random.Random(V4_BOOTSTRAP_SEED + 17)
    bootstrap: list[float] = []
    if increments:
        for _ in range(V4_BOOTSTRAP_ITERATIONS):
            sample = [increments[rng.randrange(len(increments))] for _ in range(len(increments))]
            bootstrap.append(mean(sample))

    v4_wealth = 1.0
    baseline_wealth = 1.0
    market_wealth = 1.0
    for cohort in cohorts:
        v4_wealth *= max(0.001, 1.0 + float(cohort["v4_stock_return_percent"]) / 100.0)
        baseline_wealth *= max(0.001, 1.0 + float(cohort["baseline_stock_return_percent"]) / 100.0)
        market_wealth *= max(0.001, 1.0 + float(cohort["market_return_percent"]) / 100.0)

    years = len(cohorts) * 20.0 / 252.0
    def cagr(wealth: float) -> float | None:
        return (wealth ** (1.0 / years) - 1.0) * 100.0 if years > 0 and wealth > 0 else None

    return {
        "cohorts": len(cohorts),
        "mean_incremental_net_excess_percent": mean(increments) if increments else None,
        "median_incremental_net_excess_percent": median(increments) if increments else None,
        "incremental_bootstrap_95": {
            "low": _percentile(bootstrap, 0.025),
            "high": _percentile(bootstrap, 0.975),
        },
        "v4_compounded_return_percent": (v4_wealth - 1.0) * 100.0 if cohorts else None,
        "baseline_compounded_return_percent": (baseline_wealth - 1.0) * 100.0 if cohorts else None,
        "nepse_compounded_return_percent": (market_wealth - 1.0) * 100.0 if cohorts else None,
        "v4_approx_cagr_percent": cagr(v4_wealth),
        "baseline_approx_cagr_percent": cagr(baseline_wealth),
        "nepse_approx_cagr_percent": cagr(market_wealth),
    }


def residual_prediction_buckets(
    rows: Sequence[dict[str, Any]],
    predictions: Sequence[float],
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
        triples = [
            (row, float(prediction), float(probability))
            for row, prediction, probability in zip(rows, predictions, probabilities)
            if low <= float(prediction) < high
        ]
        actual = [float(row["residual_alpha_percent"]) for row, _, _ in triples]
        result[label] = {
            "calls": len(triples),
            "independent_dates": len({row["date"] for row, _, _ in triples}),
            "mean_predicted_residual_percent": mean(prediction for _, prediction, _ in triples) if triples else None,
            "mean_probability_positive_residual": mean(probability for _, _, probability in triples) if triples else None,
            "actual_positive_residual_rate": mean(1.0 if value > 0.0 else 0.0 for value in actual) if actual else None,
            "mean_actual_residual_percent": mean(actual) if actual else None,
            "median_actual_residual_percent": median(actual) if actual else None,
        }
    return result
