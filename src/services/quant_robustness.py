from __future__ import annotations

import math
import random
from collections import defaultdict
from statistics import mean, median, pstdev
from typing import Any, Callable, Sequence

from src.services.quant_features import ROUND_TRIP_COST_PERCENT

DEFAULT_HIGH_CONFIDENCE_THRESHOLD = 0.65
DEFAULT_BOOTSTRAP_ITERATIONS = 1000
DEFAULT_BOOTSTRAP_SEED = 20260821
DEFAULT_COST_STRESS = (0.50, 1.00, 1.50, 2.00)


def _safe_mean(values: Sequence[float]) -> float | None:
    return mean(values) if values else None


def _wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    p = successes / total
    denominator = 1.0 + z * z / total
    center = (p + z * z / (2.0 * total)) / denominator
    margin = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * total)) / total) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = max(0.0, min(1.0, fraction)) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _bootstrap_date_clusters(
    clusters: Sequence[list[dict[str, float]]],
    metric: Callable[[list[dict[str, float]]], float | None],
    *,
    iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> dict[str, Any]:
    if not clusters:
        return {"iterations": 0, "low_95": None, "high_95": None}

    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(max(100, iterations)):
        sampled_clusters = [clusters[rng.randrange(len(clusters))] for _ in range(len(clusters))]
        flattened = [row for cluster in sampled_clusters for row in cluster]
        value = metric(flattened)
        if value is not None and math.isfinite(value):
            samples.append(float(value))

    return {
        "iterations": len(samples),
        "low_95": _percentile(samples, 0.025),
        "high_95": _percentile(samples, 0.975),
    }


def annotate_liquidity_buckets(rows: Sequence[dict[str, Any]]) -> None:
    """Attach a point-in-time cross-sectional liquidity tercile for diagnostics only.

    `trailing_turnover_20d` must be computed using data known on the row date. The
    bucket is deliberately not fed back into the model in v1; it is only used to
    test whether performance is concentrated in illiquid names.
    """
    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("trailing_turnover_20d") is not None:
            grouped[row["date"]].append(row)

    for date_rows in grouped.values():
        ordered = sorted(date_rows, key=lambda row: float(row.get("trailing_turnover_20d") or 0.0))
        count = len(ordered)
        if count < 6:
            for row in ordered:
                row["liquidity_bucket"] = "unclassified"
            continue
        for index, row in enumerate(ordered):
            percentile = (index + 0.5) / count
            if percentile <= 1.0 / 3.0:
                bucket = "low"
            elif percentile <= 2.0 / 3.0:
                bucket = "medium"
            else:
                bucket = "high"
            row["liquidity_bucket"] = bucket


def market_regime_proxy(row: dict[str, Any]) -> str:
    """Simple point-in-time market-return bucket used only for robustness slicing."""
    features = row.get("features", {})
    stock_return = float(features.get("return_20d", 0.0))
    relative = float(features.get("relative_strength_market_20d", 0.0))
    market_return = stock_return - relative
    if market_return >= 6.0:
        return "strong_positive_20d"
    if market_return >= 2.0:
        return "positive_20d"
    if market_return > -2.0:
        return "sideways_20d"
    if market_return > -6.0:
        return "negative_20d"
    return "stress_20d"


def high_confidence_diagnostics(
    rows: Sequence[dict[str, Any]],
    probabilities: Sequence[float],
    *,
    threshold: float = DEFAULT_HIGH_CONFIDENCE_THRESHOLD,
    bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
) -> dict[str, Any]:
    selected = [
        {
            "date": row["date"],
            "success": 1.0 if bool(row["success"]) else 0.0,
            "excess": float(row["excess_return_percent"]),
            "probability": float(probability),
        }
        for row, probability in zip(rows, probabilities)
        if float(probability) >= threshold
    ]
    successes = sum(int(row["success"]) for row in selected)
    precision = successes / len(selected) if selected else None
    wilson_low, wilson_high = _wilson_interval(successes, len(selected))

    grouped: dict[Any, list[dict[str, float]]] = defaultdict(list)
    for row in selected:
        grouped[row["date"]].append(row)
    clusters = list(grouped.values())

    precision_bootstrap = _bootstrap_date_clusters(
        clusters,
        lambda sample: sum(row["success"] for row in sample) / len(sample) if sample else None,
        iterations=bootstrap_iterations,
    )
    excess_bootstrap = _bootstrap_date_clusters(
        clusters,
        lambda sample: mean(row["excess"] for row in sample) if sample else None,
        iterations=bootstrap_iterations,
        seed=DEFAULT_BOOTSTRAP_SEED + 1,
    )

    return {
        "probability_threshold": threshold,
        "calls": len(selected),
        "independent_entry_dates": len(grouped),
        "precision": precision,
        "precision_wilson_95": {"low": wilson_low, "high": wilson_high},
        "precision_date_cluster_bootstrap_95": precision_bootstrap,
        "mean_excess_return_percent": _safe_mean([row["excess"] for row in selected]),
        "median_excess_return_percent": median([row["excess"] for row in selected]) if selected else None,
        "mean_excess_date_cluster_bootstrap_95": excess_bootstrap,
        "note": (
            "Wilson treats calls as Bernoulli observations; the date-cluster bootstrap is the more conservative interval "
            "when same-day NEPSE stocks are correlated."
        ),
    }


def _rank_selection(
    rows: Sequence[dict[str, Any]],
    scores: Sequence[float],
    k: int,
) -> list[tuple[Any, list[dict[str, Any]]]]:
    grouped: dict[Any, list[tuple[dict[str, Any], float]]] = defaultdict(list)
    for row, score in zip(rows, scores):
        grouped[row["date"]].append((row, float(score)))

    selected: list[tuple[Any, list[dict[str, Any]]]] = []
    for trading_date in sorted(grouped):
        date_rows = grouped[trading_date]
        if len(date_rows) < k:
            continue
        ranked = sorted(date_rows, key=lambda item: item[1], reverse=True)[:k]
        selected.append((trading_date, [row for row, _ in ranked]))
    return selected


def top_k_sweep(
    rows: Sequence[dict[str, Any]],
    rank_scores: Sequence[float],
    *,
    ks: Sequence[int] = (5, 10, 20),
    cost_percent: float = ROUND_TRIP_COST_PERCENT,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for k in ks:
        cohorts = _rank_selection(rows, rank_scores, k)
        precisions: list[float] = []
        net_excess: list[float] = []
        for _, selected in cohorts:
            precisions.append(mean(1.0 if float(row["excess_return_percent"]) > cost_percent else 0.0 for row in selected))
            net_excess.append(mean(float(row["excess_return_percent"]) - cost_percent for row in selected))
        result[f"p_at_{k}"] = {
            "dates": len(cohorts),
            "precision_after_cost": _safe_mean(precisions),
            "mean_net_excess_return_percent": _safe_mean(net_excess),
        }
    return result


def cost_stress_test(
    rows: Sequence[dict[str, Any]],
    probabilities: Sequence[float],
    rank_scores: Sequence[float],
    *,
    costs: Sequence[float] = DEFAULT_COST_STRESS,
    high_confidence_threshold: float = DEFAULT_HIGH_CONFIDENCE_THRESHOLD,
    rank_k: int = 10,
) -> dict[str, Any]:
    high_conf = [
        row
        for row, probability in zip(rows, probabilities)
        if float(probability) >= high_confidence_threshold
    ]
    rank_cohorts = _rank_selection(rows, rank_scores, rank_k)

    result: dict[str, Any] = {}
    for cost in costs:
        high_precision = (
            mean(1.0 if float(row["excess_return_percent"]) > cost else 0.0 for row in high_conf)
            if high_conf
            else None
        )
        high_net_excess = (
            mean(float(row["excess_return_percent"]) - cost for row in high_conf)
            if high_conf
            else None
        )
        rank_precisions: list[float] = []
        rank_returns: list[float] = []
        for _, selected in rank_cohorts:
            rank_precisions.append(mean(1.0 if float(row["excess_return_percent"]) > cost else 0.0 for row in selected))
            rank_returns.append(mean(float(row["excess_return_percent"]) - cost for row in selected))

        result[f"cost_{cost:.2f}pct"] = {
            "cost_percent": cost,
            "high_confidence_calls": len(high_conf),
            "high_confidence_precision": high_precision,
            "high_confidence_mean_net_excess_percent": high_net_excess,
            f"rank_p_at_{rank_k}": _safe_mean(rank_precisions),
            f"rank_top_{rank_k}_mean_net_excess_percent": _safe_mean(rank_returns),
            "rank_dates": len(rank_cohorts),
        }

    result["note"] = (
        "Probabilities were calibrated to the production cost hurdle; higher-cost rows are stress tests, not separately calibrated probabilities."
    )
    return result


def _max_drawdown(values: Sequence[float]) -> float | None:
    if not values:
        return None
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst * 100.0


def non_overlapping_portfolio_backtest(
    rows: Sequence[dict[str, Any]],
    rank_scores: Sequence[float],
    *,
    k: int = 10,
    cost_percent: float = ROUND_TRIP_COST_PERCENT,
) -> dict[str, Any]:
    """Construct conservative independent 20-day cohorts.

    Once a top-K cohort is selected, all candidate dates are skipped until every
    selected position's labeled horizon has ended. This intentionally sacrifices
    sample size to avoid presenting overlapping 20-day windows as independent
    portfolio observations.
    """
    cohorts = _rank_selection(rows, rank_scores, k)
    chosen: list[dict[str, Any]] = []
    active_until = None

    for trading_date, selected in cohorts:
        if active_until is not None and trading_date <= active_until:
            continue
        end_dates = [row.get("label_end_date") for row in selected if row.get("label_end_date") is not None]
        if not end_dates:
            continue
        active_until = max(end_dates)
        stock_return = mean(float(row["stock_return_percent"]) for row in selected)
        market_return = mean(float(row["market_return_percent"]) for row in selected)
        net_stock_return = stock_return - cost_percent
        net_excess = net_stock_return - market_return
        chosen.append(
            {
                "date": trading_date,
                "end_date": active_until,
                "holdings": len(selected),
                "gross_stock_return_percent": stock_return,
                "net_stock_return_percent": net_stock_return,
                "market_return_percent": market_return,
                "net_excess_return_percent": net_excess,
                "mean_max_adverse_percent": mean(float(row["max_adverse_percent"]) for row in selected),
            }
        )

    portfolio_wealth = 1.0
    market_wealth = 1.0
    portfolio_curve = [1.0]
    market_curve = [1.0]
    for cohort in chosen:
        portfolio_wealth *= max(0.001, 1.0 + float(cohort["net_stock_return_percent"]) / 100.0)
        market_wealth *= max(0.001, 1.0 + float(cohort["market_return_percent"]) / 100.0)
        portfolio_curve.append(portfolio_wealth)
        market_curve.append(market_wealth)

    years = len(chosen) * 20.0 / 252.0
    portfolio_cagr = (portfolio_wealth ** (1.0 / years) - 1.0) * 100.0 if years > 0 and portfolio_wealth > 0 else None
    market_cagr = (market_wealth ** (1.0 / years) - 1.0) * 100.0 if years > 0 and market_wealth > 0 else None
    excess_values = [float(row["net_excess_return_percent"]) for row in chosen]

    rng = random.Random(DEFAULT_BOOTSTRAP_SEED + 7)
    bootstrap_means: list[float] = []
    if chosen:
        for _ in range(DEFAULT_BOOTSTRAP_ITERATIONS):
            sample = [excess_values[rng.randrange(len(excess_values))] for _ in range(len(excess_values))]
            bootstrap_means.append(mean(sample))

    return {
        "top_k": k,
        "round_trip_cost_percent": cost_percent,
        "cohorts": len(chosen),
        "first_entry_date": chosen[0]["date"].isoformat() if chosen and hasattr(chosen[0]["date"], "isoformat") else None,
        "last_entry_date": chosen[-1]["date"].isoformat() if chosen and hasattr(chosen[-1]["date"], "isoformat") else None,
        "mean_net_excess_return_percent": _safe_mean(excess_values),
        "median_net_excess_return_percent": median(excess_values) if excess_values else None,
        "positive_excess_cohort_rate": mean(1.0 if value > 0 else 0.0 for value in excess_values) if excess_values else None,
        "mean_net_excess_bootstrap_95": {
            "low": _percentile(bootstrap_means, 0.025),
            "high": _percentile(bootstrap_means, 0.975),
        },
        "portfolio_compounded_return_percent": (portfolio_wealth - 1.0) * 100.0 if chosen else None,
        "nepse_compounded_return_percent": (market_wealth - 1.0) * 100.0 if chosen else None,
        "portfolio_approx_cagr_percent": portfolio_cagr,
        "nepse_approx_cagr_percent": market_cagr,
        "portfolio_max_drawdown_percent": _max_drawdown(portfolio_curve),
        "nepse_max_drawdown_percent": _max_drawdown(market_curve),
        "mean_selected_path_adverse_percent": _safe_mean([float(row["mean_max_adverse_percent"]) for row in chosen]),
        "recent_cohorts": [
            {
                **row,
                "date": row["date"].isoformat() if hasattr(row["date"], "isoformat") else str(row["date"]),
                "end_date": row["end_date"].isoformat() if hasattr(row["end_date"], "isoformat") else str(row["end_date"]),
            }
            for row in chosen[-8:]
        ],
        "note": (
            "This is a conservative non-overlapping cohort backtest, not a daily marked-to-market execution simulator. "
            "Approximate CAGR assumes each independent cohort represents 20 trading days."
        ),
    }


def _group_key(row: dict[str, Any], dimension: str) -> str:
    if dimension == "year":
        return str(getattr(row.get("date"), "year", "unknown"))
    if dimension == "sector":
        return str(row.get("sector") or "unknown")
    if dimension == "liquidity":
        return str(row.get("liquidity_bucket") or "unclassified")
    if dimension == "market_regime_proxy":
        return market_regime_proxy(row)
    raise ValueError(f"Unknown robustness dimension: {dimension}")


def classifier_breakdown(
    rows: Sequence[dict[str, Any]],
    probabilities: Sequence[float],
    *,
    dimension: str,
    high_confidence_threshold: float = DEFAULT_HIGH_CONFIDENCE_THRESHOLD,
) -> dict[str, Any]:
    grouped: dict[str, list[tuple[dict[str, Any], float]]] = defaultdict(list)
    for row, probability in zip(rows, probabilities):
        grouped[_group_key(row, dimension)].append((row, float(probability)))

    result: dict[str, Any] = {}
    for label, pairs in sorted(grouped.items()):
        group_rows = [row for row, _ in pairs]
        group_probabilities = [probability for _, probability in pairs]
        high = [
            (row, probability)
            for row, probability in pairs
            if probability >= high_confidence_threshold
        ]
        brier = mean(
            (probability - (1.0 if bool(row["success"]) else 0.0)) ** 2
            for row, probability in pairs
        ) if pairs else None
        result[label] = {
            "rows": len(pairs),
            "independent_dates": len({row["date"] for row in group_rows}),
            "base_success_rate": mean(1.0 if bool(row["success"]) else 0.0 for row in group_rows) if group_rows else None,
            "brier_score": brier,
            "mean_excess_return_percent": _safe_mean([float(row["excess_return_percent"]) for row in group_rows]),
            "high_confidence_calls": len(high),
            "high_confidence_dates": len({row["date"] for row, _ in high}),
            "high_confidence_precision": (
                mean(1.0 if bool(row["success"]) else 0.0 for row, _ in high)
                if high
                else None
            ),
            "high_confidence_mean_excess_return_percent": (
                mean(float(row["excess_return_percent"]) for row, _ in high)
                if high
                else None
            ),
        }
    return result


def build_robustness_report(
    rows: Sequence[dict[str, Any]],
    probabilities: Sequence[float],
    rank_scores: Sequence[float],
) -> dict[str, Any]:
    annotate_liquidity_buckets(rows)
    return {
        "high_confidence": high_confidence_diagnostics(rows, probabilities),
        "top_k_sweep": top_k_sweep(rows, rank_scores),
        "cost_stress": cost_stress_test(rows, probabilities, rank_scores),
        "non_overlapping_top10_portfolio": non_overlapping_portfolio_backtest(rows, rank_scores, k=10),
        "breakdowns": {
            "year": classifier_breakdown(rows, probabilities, dimension="year"),
            "sector": classifier_breakdown(rows, probabilities, dimension="sector"),
            "market_regime_proxy": classifier_breakdown(rows, probabilities, dimension="market_regime_proxy"),
            "liquidity": classifier_breakdown(rows, probabilities, dimension="liquidity"),
        },
        "diagnostic_scope": {
            "uses_untouched_test_rows_only": True,
            "market_regime_definition": "20-day NEPSE return proxy inferred point-in-time from stock return minus market-relative strength",
            "liquidity_definition": "same-date tercile of trailing 20-day average turnover; diagnostic only",
            "survivorship_bias_resolved": False,
        },
    }
