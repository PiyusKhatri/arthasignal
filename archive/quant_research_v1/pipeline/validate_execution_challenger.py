from __future__ import annotations

import argparse
import json
import logging
from statistics import mean
from typing import Any

from src.pipeline.train_quant_models import DEFAULT_SYMBOL_LIMIT, _build_pooled_rows, _rank_scores
from archive.quant_research_v1.services.quant_execution_aware import (
    EXECUTION_HURDLE_PERCENT,
    OUTER_FOLDS,
    SEVERE_DRAWDOWN_THRESHOLD_PERCENT,
    execution_aware_scores,
    expanding_nested_folds,
    relabel_downside_rows,
    relabel_execution_rows,
    selected_downside_rate,
)
from src.services.quant_features import FEATURE_SCALES, evaluate_predictions
from src.services.quant_robustness import (
    cost_stress_test,
    high_confidence_diagnostics,
    non_overlapping_portfolio_backtest,
    top_k_sweep,
)
from src.services.quant_xgboost import (
    calibrate_probability,
    fit_platt_calibrator,
    fit_xgb_classifier,
    fit_xgb_ranker,
    predict_xgb_rows,
    xgboost_available,
)

logger = logging.getLogger(__name__)
EXECUTION_CHALLENGER_VERSION = "artha-xgb-execution-aware-v2-research"
POLICY_VERSION = "2026-08-21-execution-v1"
MIN_VALID_FOLDS = 3
MIN_HIGH_CONFIDENCE_CALLS = 20
MIN_HIGH_CONFIDENCE_DATES = 10


def _normalized_feature(row: dict[str, Any], name: str) -> float:
    raw = float(row.get("features", {}).get(name, 0.0))
    scale = float(FEATURE_SCALES[name])
    return max(-5.0, min(5.0, raw / scale)) if scale > 0 else raw


def _fixed_multifactor_score(row: dict[str, Any]) -> float:
    return (
        1.00 * _normalized_feature(row, "relative_strength_market_20d")
        + 0.75 * _normalized_feature(row, "relative_strength_sector_20d")
        + 0.40 * _normalized_feature(row, "return_60d")
        + 0.30 * _normalized_feature(row, "distance_sma50_percent")
        + 0.20 * _normalized_feature(row, "turnover_ratio_20d")
        - 0.20 * _normalized_feature(row, "annualized_volatility_20d")
    )


def _baseline_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    score_sets = {
        "market_relative_momentum": [
            float(row["features"].get("relative_strength_market_20d", 0.0)) for row in rows
        ],
        "sector_relative_momentum": [
            float(row["features"].get("relative_strength_sector_20d", 0.0)) for row in rows
        ],
        "fixed_multifactor": [_fixed_multifactor_score(row) for row in rows],
    }
    return {
        name: top_k_sweep(
            rows,
            scores,
            ks=(5, 10, 20),
            cost_percent=EXECUTION_HURDLE_PERCENT,
        )
        for name, scores in score_sets.items()
    }


def _best_baseline_p10(baselines: dict[str, Any]) -> float | None:
    values = [
        report.get("p_at_10", {}).get("precision_after_cost")
        for report in baselines.values()
    ]
    usable = [float(value) for value in values if value is not None]
    return max(usable) if usable else None


def _calibrated_predictions(
    model: Any,
    calibration_rows: list[dict[str, Any]],
    test_rows: list[dict[str, Any]],
) -> tuple[list[float], dict[str, float] | None]:
    calibration_raw = predict_xgb_rows(model, calibration_rows)
    calibrator = fit_platt_calibrator(
        calibration_raw,
        [bool(row["success"]) for row in calibration_rows],
    )
    test_raw = predict_xgb_rows(model, test_rows)
    calibrated = [calibrate_probability(value, calibrator) for value in test_raw]
    if any(value is None for value in calibrated):
        return [], calibrator
    return [float(value) for value in calibrated if value is not None], calibrator


def _fold_result(fold: dict[str, Any]) -> dict[str, Any] | None:
    train = fold["train"]
    calibration = fold["calibration"]
    test = fold["test"]

    execution_train = relabel_execution_rows(train)
    execution_calibration = relabel_execution_rows(calibration)
    execution_test = relabel_execution_rows(test)
    risk_train = relabel_downside_rows(train)
    risk_calibration = relabel_downside_rows(calibration)
    risk_test = relabel_downside_rows(test)

    execution_model = fit_xgb_classifier(execution_train)
    risk_model = fit_xgb_classifier(risk_train)
    ranker = fit_xgb_ranker(train)
    if execution_model is None or risk_model is None or ranker is None:
        return None

    execution_probabilities, execution_calibrator = _calibrated_predictions(
        execution_model,
        execution_calibration,
        execution_test,
    )
    risk_probabilities, risk_calibrator = _calibrated_predictions(
        risk_model,
        risk_calibration,
        risk_test,
    )
    rank_scores = _rank_scores(ranker, test)
    if not execution_probabilities or not risk_probabilities or len(rank_scores) != len(test):
        return None

    composite_scores = execution_aware_scores(
        test,
        execution_probabilities,
        risk_probabilities,
        rank_scores,
    )
    if len(composite_scores) != len(test):
        return None

    execution_metrics = evaluate_predictions(
        execution_probabilities,
        [bool(row["success"]) for row in execution_test],
        [float(row["excess_return_percent"]) for row in execution_test],
    )
    risk_metrics = evaluate_predictions(
        risk_probabilities,
        [bool(row["success"]) for row in risk_test],
        [float(row["max_adverse_percent"]) for row in risk_test],
    )
    top_k = top_k_sweep(
        test,
        composite_scores,
        ks=(5, 10, 20),
        cost_percent=EXECUTION_HURDLE_PERCENT,
    )
    baselines = _baseline_report(test)
    risk_selection = selected_downside_rate(test, composite_scores, k=10)

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
        "execution": execution_metrics,
        "risk": risk_metrics,
        "top_k": top_k,
        "risk_selection": risk_selection,
        "baselines": baselines,
        "best_baseline_p10": _best_baseline_p10(baselines),
        "execution_calibration_available": execution_calibrator is not None,
        "risk_calibration_available": risk_calibrator is not None,
        "_rows": test,
        "_execution_probabilities": execution_probabilities,
        "_risk_probabilities": risk_probabilities,
        "_composite_scores": composite_scores,
    }


def validate_execution_challenger(
    *,
    limit: int = DEFAULT_SYMBOL_LIMIT,
    folds: int = OUTER_FOLDS,
) -> dict[str, Any]:
    if not xgboost_available():
        return {
            "status": "xgboost_unavailable",
            "model_version": EXECUTION_CHALLENGER_VERSION,
        }

    pooled, universe = _build_pooled_rows(limit=limit)
    fold_specs = expanding_nested_folds(pooled, folds=folds)
    if not fold_specs:
        return {
            "status": "insufficient_nested_history",
            "model_version": EXECUTION_CHALLENGER_VERSION,
            "pooled_rows": len(pooled),
            "universe": universe,
        }

    raw_results: list[dict[str, Any]] = []
    for fold in fold_specs:
        logger.info(
            "Execution-aware outer fold %s: train=%d calibration=%d test=%d",
            fold["fold"],
            len(fold["train"]),
            len(fold["calibration"]),
            len(fold["test"]),
        )
        result = _fold_result(fold)
        if result is not None:
            raw_results.append(result)

    if not raw_results:
        return {
            "status": "no_valid_folds",
            "model_version": EXECUTION_CHALLENGER_VERSION,
            "requested_folds": len(fold_specs),
            "universe": universe,
        }

    all_rows: list[dict[str, Any]] = []
    all_execution_probabilities: list[float] = []
    all_risk_probabilities: list[float] = []
    all_composite_scores: list[float] = []
    fold_summaries: list[dict[str, Any]] = []

    for result in raw_results:
        all_rows.extend(result.pop("_rows"))
        all_execution_probabilities.extend(result.pop("_execution_probabilities"))
        all_risk_probabilities.extend(result.pop("_risk_probabilities"))
        all_composite_scores.extend(result.pop("_composite_scores"))
        fold_summaries.append(result)

    execution_rows = relabel_execution_rows(all_rows)
    risk_rows = relabel_downside_rows(all_rows)
    execution_metrics = evaluate_predictions(
        all_execution_probabilities,
        [bool(row["success"]) for row in execution_rows],
        [float(row["excess_return_percent"]) for row in execution_rows],
    )
    risk_metrics = evaluate_predictions(
        all_risk_probabilities,
        [bool(row["success"]) for row in risk_rows],
        [float(row["max_adverse_percent"]) for row in risk_rows],
    )
    high_confidence = high_confidence_diagnostics(
        execution_rows,
        all_execution_probabilities,
        threshold=0.65,
    )
    top_k = top_k_sweep(
        all_rows,
        all_composite_scores,
        ks=(5, 10, 20),
        cost_percent=EXECUTION_HURDLE_PERCENT,
    )
    cost_stress = cost_stress_test(
        all_rows,
        all_execution_probabilities,
        all_composite_scores,
        costs=(1.00, 1.50, 2.00),
        rank_k=10,
    )
    portfolio = non_overlapping_portfolio_backtest(
        all_rows,
        all_composite_scores,
        k=10,
        cost_percent=EXECUTION_HURDLE_PERCENT,
    )
    risk_selection = selected_downside_rate(all_rows, all_composite_scores, k=10)
    baselines = _baseline_report(all_rows)
    best_baseline = _best_baseline_p10(baselines)
    model_p10 = top_k.get("p_at_10", {}).get("precision_after_cost")

    positive_fold_count = sum(
        1
        for fold in fold_summaries
        if (fold.get("top_k", {}).get("p_at_10", {}).get("mean_net_excess_return_percent") or 0.0) > 0.0
    )
    beating_baseline_fold_count = sum(
        1
        for fold in fold_summaries
        if fold.get("top_k", {}).get("p_at_10", {}).get("precision_after_cost") is not None
        and (
            fold.get("best_baseline_p10") is None
            or float(fold["top_k"]["p_at_10"]["precision_after_cost"]) > float(fold["best_baseline_p10"])
        )
    )

    portfolio_low = portfolio.get("mean_net_excess_bootstrap_95", {}).get("low")
    selected_risk = risk_selection.get("selected_severe_drawdown_rate")
    universe_risk = risk_selection.get("universe_severe_drawdown_rate")
    checks = {
        "enough_valid_outer_folds": len(fold_summaries) >= MIN_VALID_FOLDS,
        "most_folds_positive_after_1pct_cost": positive_fold_count >= max(2, len(fold_summaries) - 1),
        "most_folds_beat_stronger_baselines": beating_baseline_fold_count >= max(2, len(fold_summaries) - 1),
        "aggregate_top10_beats_stronger_baselines": (
            model_p10 is not None
            and (best_baseline is None or float(model_p10) > float(best_baseline))
        ),
        "aggregate_top10_net_excess_positive_at_1pct": (
            top_k.get("p_at_10", {}).get("mean_net_excess_return_percent") is not None
            and float(top_k["p_at_10"]["mean_net_excess_return_percent"]) > 0.0
        ),
        "downside_rate_reduced_in_top10": (
            selected_risk is not None
            and universe_risk is not None
            and float(selected_risk) < float(universe_risk)
        ),
        "enough_high_confidence_density": (
            int(high_confidence.get("calls") or 0) >= MIN_HIGH_CONFIDENCE_CALLS
            and int(high_confidence.get("independent_entry_dates") or 0) >= MIN_HIGH_CONFIDENCE_DATES
        ),
        "non_overlap_bootstrap_lower_bound_positive": (
            portfolio_low is not None and float(portfolio_low) > 0.0
        ),
    }
    passed = all(checks.values())

    return {
        "status": "ready",
        "model_version": EXECUTION_CHALLENGER_VERSION,
        "policy_version": POLICY_VERSION,
        "development_verdict": "candidate_for_forward_shadow" if passed else "continue_research",
        "target": {
            "execution_success": f"20D stock excess return vs NEPSE > {EXECUTION_HURDLE_PERCENT:.2f}%",
            "severe_drawdown": f"20D maximum adverse excursion <= {SEVERE_DRAWDOWN_THRESHOLD_PERCENT:.2f}%",
            "selection_score": "60% calibrated execution probability + 25% rank percentile + 15% downside safety",
        },
        "pooled_rows": len(pooled),
        "valid_outer_folds": len(fold_summaries),
        "total_nested_test_rows": len(all_rows),
        "total_nested_test_dates": len({row["date"] for row in all_rows}),
        "execution_metrics": execution_metrics,
        "risk_metrics": risk_metrics,
        "high_confidence": high_confidence,
        "top_k": top_k,
        "cost_stress": cost_stress,
        "non_overlapping_portfolio": portfolio,
        "risk_selection": risk_selection,
        "stronger_baselines": baselines,
        "best_stronger_baseline_p10": best_baseline,
        "model_p10": model_p10,
        "fold_consistency": {
            "positive_after_1pct_cost_folds": positive_fold_count,
            "beats_baseline_folds": beating_baseline_fold_count,
            "folds": fold_summaries,
        },
        "research_gate": {
            "status": "pass" if passed else "review",
            "checks": checks,
        },
        "universe": universe,
        "interpretation": {
            "fresh_untouched_historical_holdout": False,
            "reason": (
                "The v1 holdout has already been inspected. This nested walk-forward is development evidence designed "
                "to reduce period dependence, not a new untouched proof."
            ),
            "final_confirmation_required": "separate future forward-shadow ledger before any live-model promotion",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Nested walk-forward validation for the execution-aware ArthaSignal challenger"
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_SYMBOL_LIMIT)
    parser.add_argument("--folds", type=int, default=OUTER_FOLDS)
    args = parser.parse_args()
    result = validate_execution_challenger(limit=args.limit, folds=args.folds)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
