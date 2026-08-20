from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from statistics import mean
from typing import Any

from src.pipeline.train_quant_models import DEFAULT_SYMBOL_LIMIT, _build_pooled_rows, _rank_scores
from src.services.quant_decision_policy import (
    V3_EXECUTION_HURDLE_PERCENT,
    V3_POLICY_VERSION,
    V3_RISK_THRESHOLDS,
    build_v3_candidate_scores,
    calibration_buckets,
    consensus_market_regime,
    consensus_sector_regime,
    evaluate_dynamic_selection,
    non_overlapping_dynamic_portfolio,
    select_dynamic_setups,
    universe_severe_drawdown_rate,
)
from src.services.quant_execution_aware import expanding_nested_folds, relabel_downside_rows, relabel_execution_rows
from src.services.quant_features import FEATURE_SCALES
from src.services.quant_xgboost import (
    calibrate_probability,
    fit_platt_calibrator,
    fit_xgb_classifier,
    fit_xgb_ranker,
    predict_xgb_rows,
    xgboost_available,
)

logger = logging.getLogger(__name__)
V3_MODEL_VERSION = "artha-regime-risk-decision-v3-research"
MIN_VALID_FOLDS = 3
MIN_POSITIVE_FOLDS = 3
MIN_BASELINE_BEATING_FOLDS = 3
MIN_RELATIVE_RISK_REDUCTION = 0.15
MIN_HIGH_CONFIDENCE_CALLS = 30
MIN_HIGH_CONFIDENCE_DATES = 20


def _normalized_feature(row: dict[str, Any], name: str) -> float:
    value = float(row.get("features", {}).get(name, 0.0))
    scale = float(FEATURE_SCALES[name])
    return max(-5.0, min(5.0, value / scale)) if scale > 0 else value


def _fixed_multifactor_score(row: dict[str, Any]) -> float:
    return (
        1.00 * _normalized_feature(row, "relative_strength_market_20d")
        + 0.75 * _normalized_feature(row, "relative_strength_sector_20d")
        + 0.40 * _normalized_feature(row, "return_60d")
        + 0.30 * _normalized_feature(row, "distance_sma50_percent")
        + 0.20 * _normalized_feature(row, "turnover_ratio_20d")
        - 0.20 * _normalized_feature(row, "annualized_volatility_20d")
    )


def _calibrated_predictions(
    model: Any,
    calibration_rows: list[dict[str, Any]],
    test_rows: list[dict[str, Any]],
) -> list[float]:
    calibration_raw = predict_xgb_rows(model, calibration_rows)
    calibrator = fit_platt_calibrator(
        calibration_raw,
        [bool(row["success"]) for row in calibration_rows],
    )
    if calibrator is None:
        return []
    test_raw = predict_xgb_rows(model, test_rows)
    calibrated = [calibrate_probability(value, calibrator) for value in test_raw]
    if any(value is None for value in calibrated):
        return []
    return [float(value) for value in calibrated if value is not None]


def _regime_matched_baseline(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Give the fixed multifactor baseline the same market/sector opportunity set as v3."""
    capacities = {
        "strong_positive": 10,
        "positive": 5,
        "sideways": 3,
        "negative": 0,
        "stress": 0,
    }
    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["date"]].append(row)

    selected: list[dict[str, Any]] = []
    day_summaries: list[dict[str, Any]] = []
    for trading_date in sorted(grouped):
        date_rows = grouped[trading_date]
        market = consensus_market_regime(date_rows)
        capacity = capacities[market]

        sector_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in date_rows:
            sector_groups[str(row.get("sector") or "unknown")].append(row)
        sector_labels = {
            sector: consensus_sector_regime(sector_rows)
            for sector, sector_rows in sector_groups.items()
        }

        eligible = []
        for row in date_rows:
            sector = sector_labels[str(row.get("sector") or "unknown")]
            if sector == "lagging":
                continue
            if market == "sideways" and sector != "leading":
                continue
            eligible.append(row)
        ranked = sorted(eligible, key=_fixed_multifactor_score, reverse=True)[:capacity]
        selected.extend({"row": row, "market_regime": market} for row in ranked)
        day_summaries.append(
            {
                "date": trading_date,
                "market_regime": market,
                "capacity": capacity,
                "selected": len(ranked),
            }
        )
    return {"selected": selected, "days": day_summaries}


def _fit_fold(fold: dict[str, Any]) -> dict[str, Any] | None:
    train = fold["train"]
    calibration = fold["calibration"]
    test = fold["test"]

    execution_train = relabel_execution_rows(train, hurdle_percent=V3_EXECUTION_HURDLE_PERCENT)
    execution_calibration = relabel_execution_rows(calibration, hurdle_percent=V3_EXECUTION_HURDLE_PERCENT)
    execution_model = fit_xgb_classifier(execution_train)
    ranker = fit_xgb_ranker(train)
    if execution_model is None or ranker is None:
        return None

    execution_probabilities = _calibrated_predictions(
        execution_model,
        execution_calibration,
        relabel_execution_rows(test, hurdle_percent=V3_EXECUTION_HURDLE_PERCENT),
    )
    if len(execution_probabilities) != len(test):
        return None

    risk_probability_sets: list[list[float]] = []
    for threshold in V3_RISK_THRESHOLDS:
        risk_train = relabel_downside_rows(train, threshold_percent=threshold)
        risk_calibration = relabel_downside_rows(calibration, threshold_percent=threshold)
        risk_test = relabel_downside_rows(test, threshold_percent=threshold)
        risk_model = fit_xgb_classifier(risk_train)
        if risk_model is None:
            return None
        probabilities = _calibrated_predictions(risk_model, risk_calibration, risk_test)
        if len(probabilities) != len(test):
            return None
        risk_probability_sets.append(probabilities)

    rank_scores = _rank_scores(ranker, test)
    if len(rank_scores) != len(test):
        return None

    candidates = build_v3_candidate_scores(
        test,
        execution_probabilities,
        risk_probability_sets,
        rank_scores,
    )
    if len(candidates) != len(test):
        return None

    selection = select_dynamic_setups(candidates)
    selection_metrics = evaluate_dynamic_selection(selection, cost_percent=V3_EXECUTION_HURDLE_PERCENT)
    baseline_selection = _regime_matched_baseline(test)
    baseline_metrics = evaluate_dynamic_selection(baseline_selection, cost_percent=V3_EXECUTION_HURDLE_PERCENT)

    return {
        "fold": fold["fold"],
        "period": {
            "calibration_start": fold["calibration_start"].isoformat(),
            "test_start": fold["test_start"].isoformat(),
            "test_end": fold["test_end"].isoformat(),
        },
        "sizes": {
            "train": len(train),
            "calibration": len(calibration),
            "test": len(test),
            "test_dates": len({row["date"] for row in test}),
        },
        "selection": selection_metrics,
        "regime_matched_baseline": baseline_metrics,
        "_test_rows": test,
        "_execution_probabilities": execution_probabilities,
        "_risk_probability_sets": risk_probability_sets,
        "_candidates": candidates,
        "_selection": selection,
    }


def _merge_selections(selections: list[dict[str, Any]]) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    days: list[dict[str, Any]] = []
    for selection in selections:
        selected.extend(selection.get("selected", []))
        days.extend(selection.get("days", []))
    days.sort(key=lambda row: row["date"])
    selected.sort(key=lambda candidate: (candidate["row"]["date"], -float(candidate.get("score") or 0.0)))
    return {"selected": selected, "days": days}


def _high_confidence_summary(rows: list[dict[str, Any]], probabilities: list[float]) -> dict[str, Any]:
    pairs = [
        (row, probability)
        for row, probability in zip(rows, probabilities)
        if float(probability) >= 0.65
    ]
    return {
        "calls": len(pairs),
        "independent_dates": len({row["date"] for row, _ in pairs}),
        "precision_after_1pct": (
            mean(
                1.0 if float(row["excess_return_percent"]) > V3_EXECUTION_HURDLE_PERCENT else 0.0
                for row, _ in pairs
            )
            if pairs
            else None
        ),
        "mean_excess_return_percent": (
            mean(float(row["excess_return_percent"]) for row, _ in pairs) if pairs else None
        ),
    }


def validate_v3_decision_policy(
    *,
    limit: int = DEFAULT_SYMBOL_LIMIT,
    folds: int = 4,
) -> dict[str, Any]:
    if not xgboost_available():
        return {"status": "xgboost_unavailable", "model_version": V3_MODEL_VERSION}

    pooled, universe = _build_pooled_rows(limit=limit)
    fold_specs = expanding_nested_folds(pooled, folds=folds)
    if not fold_specs:
        return {
            "status": "insufficient_nested_history",
            "model_version": V3_MODEL_VERSION,
            "pooled_rows": len(pooled),
            "universe": universe,
        }

    raw_results: list[dict[str, Any]] = []
    for fold in fold_specs:
        logger.info(
            "V3 regime/risk fold %s: train=%d calibration=%d test=%d",
            fold["fold"],
            len(fold["train"]),
            len(fold["calibration"]),
            len(fold["test"]),
        )
        result = _fit_fold(fold)
        if result is not None:
            raw_results.append(result)

    if not raw_results:
        return {
            "status": "no_valid_folds",
            "model_version": V3_MODEL_VERSION,
            "requested_folds": len(fold_specs),
        }

    all_rows: list[dict[str, Any]] = []
    all_execution_probabilities: list[float] = []
    all_selections: list[dict[str, Any]] = []
    fold_summaries: list[dict[str, Any]] = []

    for result in raw_results:
        all_rows.extend(result.pop("_test_rows"))
        all_execution_probabilities.extend(result.pop("_execution_probabilities"))
        result.pop("_risk_probability_sets")
        result.pop("_candidates")
        all_selections.append(result.pop("_selection"))
        fold_summaries.append(result)

    aggregate_selection = _merge_selections(all_selections)
    aggregate_metrics = evaluate_dynamic_selection(
        aggregate_selection,
        cost_percent=V3_EXECUTION_HURDLE_PERCENT,
    )
    aggregate_portfolio = non_overlapping_dynamic_portfolio(
        aggregate_selection,
        cost_percent=V3_EXECUTION_HURDLE_PERCENT,
    )
    baseline_selection = _regime_matched_baseline(all_rows)
    baseline_metrics = evaluate_dynamic_selection(
        baseline_selection,
        cost_percent=V3_EXECUTION_HURDLE_PERCENT,
    )
    baseline_portfolio = non_overlapping_dynamic_portfolio(
        baseline_selection,
        cost_percent=V3_EXECUTION_HURDLE_PERCENT,
    )

    calibration = calibration_buckets(all_rows, all_execution_probabilities)
    high_confidence = _high_confidence_summary(all_rows, all_execution_probabilities)
    universe_risk = universe_severe_drawdown_rate(all_rows)
    selected_risk = aggregate_metrics.get("severe_drawdown_rate")
    relative_risk_reduction = (
        1.0 - float(selected_risk) / float(universe_risk)
        if selected_risk is not None and universe_risk not in (None, 0.0)
        else None
    )

    positive_folds = sum(
        1
        for fold in fold_summaries
        if (fold.get("selection", {}).get("mean_net_excess_return_percent") or 0.0) > 0.0
    )
    baseline_beating_folds = sum(
        1
        for fold in fold_summaries
        if fold.get("selection", {}).get("mean_net_excess_return_percent") is not None
        and fold.get("regime_matched_baseline", {}).get("mean_net_excess_return_percent") is not None
        and float(fold["selection"]["mean_net_excess_return_percent"])
        > float(fold["regime_matched_baseline"]["mean_net_excess_return_percent"])
    )

    portfolio_low = aggregate_portfolio.get("mean_net_excess_bootstrap_95", {}).get("low")
    portfolio_dd = aggregate_portfolio.get("portfolio_max_drawdown_percent")
    market_dd = aggregate_portfolio.get("nepse_max_drawdown_percent")
    checks = {
        "enough_valid_outer_folds": len(fold_summaries) >= MIN_VALID_FOLDS,
        "positive_after_1pct_in_at_least_3_folds": positive_folds >= MIN_POSITIVE_FOLDS,
        "beats_regime_matched_baseline_in_at_least_3_folds": baseline_beating_folds >= MIN_BASELINE_BEATING_FOLDS,
        "aggregate_mean_net_excess_positive": (
            aggregate_metrics.get("mean_net_excess_return_percent") is not None
            and float(aggregate_metrics["mean_net_excess_return_percent"]) > 0.0
        ),
        "aggregate_median_net_excess_positive": (
            aggregate_metrics.get("median_net_excess_return_percent") is not None
            and float(aggregate_metrics["median_net_excess_return_percent"]) > 0.0
        ),
        "aggregate_mean_beats_regime_matched_baseline": (
            aggregate_metrics.get("mean_net_excess_return_percent") is not None
            and baseline_metrics.get("mean_net_excess_return_percent") is not None
            and float(aggregate_metrics["mean_net_excess_return_percent"])
            > float(baseline_metrics["mean_net_excess_return_percent"])
        ),
        "severe_drawdown_relative_reduction_at_least_15pct": (
            relative_risk_reduction is not None
            and float(relative_risk_reduction) >= MIN_RELATIVE_RISK_REDUCTION
        ),
        "high_confidence_density_adequate": (
            int(high_confidence.get("calls") or 0) >= MIN_HIGH_CONFIDENCE_CALLS
            and int(high_confidence.get("independent_dates") or 0) >= MIN_HIGH_CONFIDENCE_DATES
        ),
        "non_overlap_bootstrap_lower_bound_positive": (
            portfolio_low is not None and float(portfolio_low) > 0.0
        ),
        "portfolio_drawdown_not_more_than_25pct_worse_than_nepse": (
            portfolio_dd is not None
            and market_dd is not None
            and abs(float(portfolio_dd)) <= abs(float(market_dd)) * 1.25
        ),
    }
    passed = all(checks.values())

    return {
        "status": "ready",
        "model_version": V3_MODEL_VERSION,
        "policy_version": V3_POLICY_VERSION,
        "development_verdict": "candidate_for_forward_shadow" if passed else "continue_research",
        "policy": {
            "execution_hurdle_percent": V3_EXECUTION_HURDLE_PERCENT,
            "risk_thresholds_percent": list(V3_RISK_THRESHOLDS),
            "dynamic_capacity": {
                "strong_positive": 10,
                "positive": 5,
                "sideways": 3,
                "negative": 0,
                "stress": 0,
            },
            "abstention": "negative/stress markets, lagging sectors, excessive risk, or insufficient probability",
            "score": "55% execution probability + 20% rank percentile + 25% downside safety",
            "regime_consensus": "same-date market median and same-date/sector median",
        },
        "pooled_rows": len(pooled),
        "valid_outer_folds": len(fold_summaries),
        "total_nested_test_rows": len(all_rows),
        "total_nested_test_dates": len({row["date"] for row in all_rows}),
        "dynamic_selection": aggregate_metrics,
        "non_overlapping_portfolio": aggregate_portfolio,
        "regime_matched_baseline": baseline_metrics,
        "regime_matched_baseline_portfolio": baseline_portfolio,
        "risk": {
            "universe_severe_drawdown_rate": universe_risk,
            "selected_severe_drawdown_rate": selected_risk,
            "relative_risk_reduction": relative_risk_reduction,
        },
        "high_confidence": high_confidence,
        "calibration_buckets": calibration,
        "fold_consistency": {
            "positive_after_1pct_folds": positive_folds,
            "beats_regime_matched_baseline_folds": baseline_beating_folds,
            "folds": fold_summaries,
        },
        "research_gate": {"status": "pass" if passed else "review", "checks": checks},
        "universe": universe,
        "interpretation": {
            "fresh_untouched_historical_holdout": False,
            "development_evidence_only": True,
            "reason": (
                "V1 and V2 historical results have already been inspected. V3 is a predeclared decision-policy challenger "
                "tested through expanding folds; only future shadow observations can provide fresh confirmation."
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the v3 regime-aware, risk-gated ArthaSignal decision policy")
    parser.add_argument("--limit", type=int, default=DEFAULT_SYMBOL_LIMIT)
    parser.add_argument("--folds", type=int, default=4)
    args = parser.parse_args()
    print(json.dumps(validate_v3_decision_policy(limit=args.limit, folds=args.folds), indent=2, default=str))


if __name__ == "__main__":
    main()
