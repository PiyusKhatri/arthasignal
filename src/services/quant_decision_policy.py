from __future__ import annotations

import math
import random
from collections import defaultdict
from statistics import mean, median
from typing import Any, Sequence

from src.services.quant_features import ROUND_TRIP_COST_PERCENT

V3_EXECUTION_HURDLE_PERCENT = 1.00
V3_RISK_THRESHOLDS = (-3.0, -5.0, -8.0)
V3_POLICY_VERSION = "2026-08-21-regime-risk-v1"
BOOTSTRAP_ITERATIONS = 1000
BOOTSTRAP_SEED = 20260821

MARKET_CAPACITY = {
    "strong_positive": 10,
    "positive": 5,
    "sideways": 3,
    "negative": 0,
    "stress": 0,
}
MARKET_MIN_EXECUTION_PROBABILITY = {
    "strong_positive": 0.52,
    "positive": 0.54,
    "sideways": 0.60,
    "negative": 1.01,
    "stress": 1.01,
}
MAX_RISK_LADDER_SCORE = 0.55


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = _clamp(fraction) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def infer_market_return_20d(row: dict[str, Any]) -> float:
    features = row.get("features", {})
    stock_return = float(features.get("return_20d", 0.0))
    relative_strength = float(features.get("relative_strength_market_20d", 0.0))
    return stock_return - relative_strength


def classify_market_regime(row: dict[str, Any]) -> str:
    market_return = infer_market_return_20d(row)
    if market_return >= 6.0:
        return "strong_positive"
    if market_return >= 2.0:
        return "positive"
    if market_return > -2.0:
        return "sideways"
    if market_return > -6.0:
        return "negative"
    return "stress"


def sector_relative_strength_20d(row: dict[str, Any]) -> float:
    features = row.get("features", {})
    stock_vs_market = float(features.get("relative_strength_market_20d", 0.0))
    stock_vs_sector = float(features.get("relative_strength_sector_20d", 0.0))
    return stock_vs_market - stock_vs_sector


def classify_sector_regime(row: dict[str, Any]) -> str:
    relative = sector_relative_strength_20d(row)
    if relative >= 2.0:
        return "leading"
    if relative > -2.0:
        return "neutral"
    return "lagging"


def classify_volatility_regime(row: dict[str, Any]) -> str:
    volatility = float(row.get("features", {}).get("annualized_volatility_20d", 0.0))
    if volatility >= 40.0:
        return "high"
    if volatility >= 25.0:
        return "normal"
    return "low"


def monotonic_risk_ladder(
    probability_3pct: float,
    probability_5pct: float,
    probability_8pct: float,
) -> dict[str, float]:
    """Enforce the natural nesting P(>3% loss) >= P(>5%) >= P(>8%)."""
    p8 = _clamp(probability_8pct)
    p5 = max(_clamp(probability_5pct), p8)
    p3 = max(_clamp(probability_3pct), p5)
    score = 0.45 * p3 + 0.35 * p5 + 0.20 * p8
    return {
        "p_adverse_3pct": p3,
        "p_adverse_5pct": p5,
        "p_adverse_8pct": p8,
        "risk_score": _clamp(score),
        "safety_score": 1.0 - _clamp(score),
    }


def _date_rank_percentiles(rows: Sequence[dict[str, Any]], scores: Sequence[float]) -> list[float]:
    grouped: dict[Any, list[tuple[int, float]]] = defaultdict(list)
    for index, (row, score) in enumerate(zip(rows, scores)):
        grouped[row["date"]].append((index, float(score)))

    percentiles = [0.5] * len(rows)
    for pairs in grouped.values():
        ordered = sorted(pairs, key=lambda item: item[1])
        denominator = max(1, len(ordered) - 1)
        for position, (original_index, _) in enumerate(ordered):
            percentiles[original_index] = position / denominator
    return percentiles


def build_v3_candidate_scores(
    rows: Sequence[dict[str, Any]],
    execution_probabilities: Sequence[float],
    risk_probability_sets: Sequence[Sequence[float]],
    rank_scores: Sequence[float],
) -> list[dict[str, Any]]:
    if len(risk_probability_sets) != 3:
        return []
    if not (
        len(rows)
        == len(execution_probabilities)
        == len(risk_probability_sets[0])
        == len(risk_probability_sets[1])
        == len(risk_probability_sets[2])
        == len(rank_scores)
    ):
        return []

    rank_percentiles = _date_rank_percentiles(rows, rank_scores)
    candidates: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        risk = monotonic_risk_ladder(
            risk_probability_sets[0][index],
            risk_probability_sets[1][index],
            risk_probability_sets[2][index],
        )
        execution_probability = _clamp(execution_probabilities[index])
        rank_percentile = _clamp(rank_percentiles[index])
        # v3 intentionally gives downside safety more influence than v2.
        score = (
            0.55 * execution_probability
            + 0.20 * rank_percentile
            + 0.25 * risk["safety_score"]
        )
        candidates.append(
            {
                "row": row,
                "execution_probability": execution_probability,
                "rank_percentile": rank_percentile,
                "score": score,
                "market_regime": classify_market_regime(row),
                "sector_regime": classify_sector_regime(row),
                "volatility_regime": classify_volatility_regime(row),
                **risk,
            }
        )
    return candidates


def _candidate_is_qualified(candidate: dict[str, Any]) -> bool:
    market = candidate["market_regime"]
    if MARKET_CAPACITY.get(market, 0) <= 0:
        return False
    if candidate["sector_regime"] == "lagging":
        return False
    if float(candidate["execution_probability"]) < MARKET_MIN_EXECUTION_PROBABILITY[market]:
        return False
    if float(candidate["risk_score"]) > MAX_RISK_LADDER_SCORE:
        return False
    if candidate["volatility_regime"] == "high" and float(candidate["execution_probability"]) < 0.60:
        return False
    # Sideways markets require genuine sector leadership rather than neutral participation.
    if market == "sideways" and candidate["sector_regime"] != "leading":
        return False
    return True


def select_dynamic_setups(candidates: Sequence[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate["row"]["date"]].append(candidate)

    selected: list[dict[str, Any]] = []
    day_summaries: list[dict[str, Any]] = []
    for trading_date in sorted(grouped):
        date_candidates = grouped[trading_date]
        market = date_candidates[0]["market_regime"] if date_candidates else "stress"
        capacity = MARKET_CAPACITY.get(market, 0)
        qualified = [candidate for candidate in date_candidates if _candidate_is_qualified(candidate)]
        chosen = sorted(qualified, key=lambda candidate: candidate["score"], reverse=True)[:capacity]
        selected.extend(chosen)
        day_summaries.append(
            {
                "date": trading_date,
                "market_regime": market,
                "capacity": capacity,
                "universe": len(date_candidates),
                "qualified": len(qualified),
                "selected": len(chosen),
            }
        )

    return {"selected": selected, "days": day_summaries}


def _max_drawdown(values: Sequence[float]) -> float | None:
    if not values:
        return None
    peak = float(values[0])
    worst = 0.0
    for raw in values:
        value = float(raw)
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst * 100.0


def evaluate_dynamic_selection(
    selection: dict[str, Any],
    *,
    cost_percent: float = V3_EXECUTION_HURDLE_PERCENT,
) -> dict[str, Any]:
    selected = selection.get("selected", [])
    days = selection.get("days", [])
    rows = [candidate["row"] for candidate in selected]
    net_excess = [float(row["excess_return_percent"]) - cost_percent for row in rows]
    severe = [float(row.get("max_adverse_percent") or 0.0) <= -5.0 for row in rows]
    active_days = {candidate["row"]["date"] for candidate in selected}
    eligible_days = [day for day in days if int(day.get("capacity") or 0) > 0]

    return {
        "selected_rows": len(rows),
        "active_dates": len(active_days),
        "eligible_regime_dates": len(eligible_days),
        "abstention_rate_on_eligible_dates": (
            1.0 - len(active_days) / len(eligible_days) if eligible_days else None
        ),
        "average_names_per_active_date": len(rows) / len(active_days) if active_days else None,
        "precision_after_cost": (
            mean(1.0 if value > 0.0 else 0.0 for value in net_excess) if net_excess else None
        ),
        "mean_net_excess_return_percent": mean(net_excess) if net_excess else None,
        "median_net_excess_return_percent": median(net_excess) if net_excess else None,
        "severe_drawdown_rate": mean(1.0 if flag else 0.0 for flag in severe) if severe else None,
        "market_regime_distribution": {
            regime: sum(candidate["market_regime"] == regime for candidate in selected)
            for regime in MARKET_CAPACITY
        },
    }


def non_overlapping_dynamic_portfolio(
    selection: dict[str, Any],
    *,
    cost_percent: float = V3_EXECUTION_HURDLE_PERCENT,
) -> dict[str, Any]:
    by_date: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for candidate in selection.get("selected", []):
        by_date[candidate["row"]["date"]].append(candidate)

    cohorts: list[dict[str, Any]] = []
    active_until = None
    for trading_date in sorted(by_date):
        if active_until is not None and trading_date <= active_until:
            continue
        candidates = by_date[trading_date]
        rows = [candidate["row"] for candidate in candidates]
        end_dates = [row.get("label_end_date") for row in rows if row.get("label_end_date") is not None]
        if not end_dates:
            continue
        active_until = max(end_dates)
        stock_return = mean(float(row["stock_return_percent"]) for row in rows)
        market_return = mean(float(row["market_return_percent"]) for row in rows)
        net_stock_return = stock_return - cost_percent
        cohorts.append(
            {
                "date": trading_date,
                "end_date": active_until,
                "holdings": len(rows),
                "net_stock_return_percent": net_stock_return,
                "market_return_percent": market_return,
                "net_excess_return_percent": net_stock_return - market_return,
                "mean_adverse_percent": mean(float(row.get("max_adverse_percent") or 0.0) for row in rows),
            }
        )

    excess = [float(cohort["net_excess_return_percent"]) for cohort in cohorts]
    rng = random.Random(BOOTSTRAP_SEED + 31)
    bootstrap: list[float] = []
    if excess:
        for _ in range(BOOTSTRAP_ITERATIONS):
            sample = [excess[rng.randrange(len(excess))] for _ in range(len(excess))]
            bootstrap.append(mean(sample))

    portfolio_wealth = 1.0
    market_wealth = 1.0
    portfolio_curve = [1.0]
    market_curve = [1.0]
    for cohort in cohorts:
        portfolio_wealth *= max(0.001, 1.0 + float(cohort["net_stock_return_percent"]) / 100.0)
        market_wealth *= max(0.001, 1.0 + float(cohort["market_return_percent"]) / 100.0)
        portfolio_curve.append(portfolio_wealth)
        market_curve.append(market_wealth)

    years = len(cohorts) * 20.0 / 252.0
    portfolio_cagr = (portfolio_wealth ** (1.0 / years) - 1.0) * 100.0 if years > 0 else None
    market_cagr = (market_wealth ** (1.0 / years) - 1.0) * 100.0 if years > 0 else None

    return {
        "cohorts": len(cohorts),
        "mean_holdings": mean(float(row["holdings"]) for row in cohorts) if cohorts else None,
        "mean_net_excess_return_percent": mean(excess) if excess else None,
        "median_net_excess_return_percent": median(excess) if excess else None,
        "positive_excess_cohort_rate": mean(1.0 if value > 0.0 else 0.0 for value in excess) if excess else None,
        "mean_net_excess_bootstrap_95": {
            "low": _percentile(bootstrap, 0.025),
            "high": _percentile(bootstrap, 0.975),
        },
        "portfolio_compounded_return_percent": (portfolio_wealth - 1.0) * 100.0 if cohorts else None,
        "nepse_compounded_return_percent": (market_wealth - 1.0) * 100.0 if cohorts else None,
        "portfolio_approx_cagr_percent": portfolio_cagr,
        "nepse_approx_cagr_percent": market_cagr,
        "portfolio_max_drawdown_percent": _max_drawdown(portfolio_curve),
        "nepse_max_drawdown_percent": _max_drawdown(market_curve),
        "mean_selected_path_adverse_percent": mean(float(row["mean_adverse_percent"]) for row in cohorts) if cohorts else None,
    }


def calibration_buckets(
    rows: Sequence[dict[str, Any]],
    probabilities: Sequence[float],
) -> dict[str, Any]:
    bucket_specs = (
        ("lt_50", 0.0, 0.50),
        ("50_55", 0.50, 0.55),
        ("55_60", 0.55, 0.60),
        ("60_65", 0.60, 0.65),
        ("65_70", 0.65, 0.70),
        ("70_plus", 0.70, 1.01),
    )
    result: dict[str, Any] = {}
    for label, low, high in bucket_specs:
        pairs = [
            (row, float(probability))
            for row, probability in zip(rows, probabilities)
            if low <= float(probability) < high
        ]
        excess = [float(row["excess_return_percent"]) for row, _ in pairs]
        severe = [float(row.get("max_adverse_percent") or 0.0) <= -5.0 for row, _ in pairs]
        result[label] = {
            "calls": len(pairs),
            "independent_dates": len({row["date"] for row, _ in pairs}),
            "mean_predicted_probability": mean(probability for _, probability in pairs) if pairs else None,
            "actual_success_rate": mean(1.0 if float(row["excess_return_percent"]) > V3_EXECUTION_HURDLE_PERCENT else 0.0 for row, _ in pairs) if pairs else None,
            "mean_excess_return_percent": mean(excess) if excess else None,
            "median_excess_return_percent": median(excess) if excess else None,
            "severe_drawdown_rate": mean(1.0 if flag else 0.0 for flag in severe) if severe else None,
        }
    return result


def universe_severe_drawdown_rate(rows: Sequence[dict[str, Any]], threshold: float = -5.0) -> float | None:
    if not rows:
        return None
    return mean(1.0 if float(row.get("max_adverse_percent") or 0.0) <= threshold else 0.0 for row in rows)
