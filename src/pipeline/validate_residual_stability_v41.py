from __future__ import annotations

import argparse
import json
import logging
from typing import Any

from src.pipeline.train_quant_models import DEFAULT_SYMBOL_LIMIT, _build_pooled_rows
from src.services.quant_execution_aware import OUTER_FOLDS, expanding_nested_folds
from src.services.quant_historical_context import attach_exact_regime_context
from src.services.quant_predictable_investability import prepare_predictable_investable_rows
from src.services.quant_residual_alpha import (
    V4_EXECUTION_HURDLE_PERCENT,
    apply_affine_calibrator,
    attach_residual_targets,
    build_baseline_candidate_pool,
    compare_same_breadth,
    evaluate_selection,
    fit_affine_calibrator,
    fit_baseline_expectation,
    fit_v4_classifier,
    fit_v4_regressor,
    matched_baseline_selection,
    non_overlapping_incremental_portfolio,
    predict_v4_classifier,
    predict_v4_regressor,
    selection_counts,
    xgboost_available,
)
from src.services.quant_residual_stability import (
    V41_POLICY_VERSION,
    apply_monotonic_residual_calibrator,
    build_v41_predictions,
    calibration_diagnostics,
    fit_monotonic_residual_calibrator,
    override_diagnostics,
    select_v41_setups,
    stability_breakdowns,
)
from src.services.quant_xgboost import calibrate_probability, fit_platt_calibrator

logger = logging.getLogger(__name__)
V41_MODEL_VERSION = "artha-residual-alpha-v4.1-stability-research"

# Intentionally unchanged from the strict V4 gate. V4.1 must improve the model,
# not relax the acceptance criteria.
MIN_VALID_FOLDS = 3
MIN_POSITIVE_FOLDS = 3
MIN_BASELINE_BEATING_FOLDS = 3
MIN_SELECTED_ROWS = 100
MIN_ACTIVE_DATES = 50
MIN_RISK_REDUCTION_VS_BASELINE = 0.10


def _calibrate_affine_target(
    model: Any,
    calibration_rows: list[dict[str, Any]],
    test_rows: list[dict[str, Any]],
    *,
    target_key: str,
    floor: float | None = None,
    ceiling: float | None = None,
) -> tuple[list[float], dict[str, float] | None]:
    calibration_raw = predict_v4_regressor(model, calibration_rows)
    if len(calibration_raw) != len(calibration_rows):
        return [], None
    calibrator = fit_affine_calibrator(
        calibration_raw,
        [float(row[target_key]) for row in calibration_rows],
    )
    if calibrator is None:
        return [], None
    test_raw = predict_v4_regressor(model, test_rows)
    if len(test_raw) != len(test_rows):
        return [], None
    values = [apply_affine_calibrator(value, calibrator) for value in test_raw]
    if floor is not None:
        values = [max(float(floor), value) for value in values]
    if ceiling is not None:
        values = [min(float(ceiling), value) for value in values]
    return values, calibrator


def _calibrate_monotonic_residual(
    model: Any,
    calibration_rows: list[dict[str, Any]],
    test_rows: list[dict[str, Any]],
) -> tuple[list[float], dict[str, Any] | None]:
    calibration_raw = predict_v4_regressor(model, calibration_rows)
    if len(calibration_raw) != len(calibration_rows):
        return [], None
    calibrator = fit_monotonic_residual_calibrator(
        calibration_raw,
        [float(row["residual_alpha_percent"]) for row in calibration_rows],
    )
    if calibrator is None:
        return [], None
    test_raw = predict_v4_regressor(model, test_rows)
    if len(test_raw) != len(test_rows):
        return [], None
    values = [
        apply_monotonic_residual_calibrator(value, calibrator)
        for value in test_raw
    ]
    return [max(-25.0, min(25.0, float(value))) for value in values], calibrator


def _calibrate_residual_probability(
    model: Any,
    calibration_rows: list[dict[str, Any]],
    test_rows: list[dict[str, Any]],
) -> tuple[list[float], dict[str, float] | None]:
    calibration_raw = predict_v4_classifier(model, calibration_rows)
    if len(calibration_raw) != len(calibration_rows):
        return [], None
    calibrator = fit_platt_calibrator(
        calibration_raw,
        [bool(row["residual_positive"]) for row in calibration_rows],
    )
    if calibrator is None:
        return [], None
    test_raw = predict_v4_classifier(model, test_rows)
    calibrated = [calibrate_probability(value, calibrator) for value in test_raw]
    if any(value is None for value in calibrated):
        return [], None
    return [float(value) for value in calibrated if value is not None], calibrator


def _fit_fold(fold: dict[str, Any]) -> dict[str, Any] | None:
    raw_train = fold["train"]
    raw_calibration = fold["calibration"]
    raw_test = fold["test"]

    train_candidates = build_baseline_candidate_pool(raw_train)
    calibration_candidates = build_baseline_candidate_pool(raw_calibration)
    test_candidates = build_baseline_candidate_pool(raw_test)
    if min(len(train_candidates), len(calibration_candidates), len(test_candidates)) < 100:
        return None

    # The expectation table sees training outcomes only. Calibration/test candidate
    # formation remains outcome-free.
    baseline_model = fit_baseline_expectation(train_candidates)
    train = attach_residual_targets(train_candidates, baseline_model)
    calibration = attach_residual_targets(calibration_candidates, baseline_model)
    test = attach_residual_targets(test_candidates, baseline_model)

    residual_model = fit_v4_regressor(
        train,
        target_key="residual_alpha_percent",
        clip_low=-25.0,
        clip_high=25.0,
    )
    residual_classifier = fit_v4_classifier(train)
    mae_model = fit_v4_regressor(
        train,
        target_key="mae_magnitude_percent",
        clip_low=0.0,
        clip_high=25.0,
    )
    mfe_model = fit_v4_regressor(
        train,
        target_key="mfe_percent",
        clip_low=0.0,
        clip_high=35.0,
    )
    if any(model is None for model in (residual_model, residual_classifier, mae_model, mfe_model)):
        return None

    residual_predictions, residual_calibrator = _calibrate_monotonic_residual(
        residual_model,
        calibration,
        test,
    )
    residual_probabilities, probability_calibrator = _calibrate_residual_probability(
        residual_classifier,
        calibration,
        test,
    )
    mae_predictions, mae_calibrator = _calibrate_affine_target(
        mae_model,
        calibration,
        test,
        target_key="mae_magnitude_percent",
        floor=0.0,
        ceiling=25.0,
    )
    mfe_predictions, mfe_calibrator = _calibrate_affine_target(
        mfe_model,
        calibration,
        test,
        target_key="mfe_percent",
        floor=0.0,
        ceiling=35.0,
    )
    if not (
        len(test)
        == len(residual_predictions)
        == len(residual_probabilities)
        == len(mae_predictions)
        == len(mfe_predictions)
    ):
        return None

    predictions = build_v41_predictions(
        test,
        residual_predictions,
        residual_probabilities,
        mae_predictions,
        mfe_predictions,
    )
    selection = select_v41_setups(predictions)
    baseline = matched_baseline_selection(test, selection_counts(selection))
    v41_metrics = evaluate_selection(selection)
    baseline_metrics = evaluate_selection(baseline)
    comparison = compare_same_breadth(selection, baseline)
    non_overlap = non_overlapping_incremental_portfolio(selection, baseline)

    return {
        "fold": fold["fold"],
        "period": {
            "calibration_start": fold["calibration_start"].isoformat(),
            "test_start": fold["test_start"].isoformat(),
            "test_end": fold["test_end"].isoformat(),
        },
        "rows": {
            "train_all": len(raw_train),
            "calibration_all": len(raw_calibration),
            "test_all": len(raw_test),
            "train_candidates": len(train),
            "calibration_candidates": len(calibration),
            "test_candidates": len(test),
        },
        "v41": v41_metrics,
        "matched_baseline": baseline_metrics,
        "incremental": comparison,
        "non_overlapping_incremental": non_overlap,
        "override_diagnostics": override_diagnostics(selection),
        "stability_breakdowns": stability_breakdowns(selection),
        "calibration": {
            "residual_monotonic": residual_calibrator,
            "residual_probability": probability_calibrator,
            "mae": mae_calibrator,
            "mfe": mfe_calibrator,
        },
        "residual_calibration_buckets": calibration_diagnostics(
            test,
            residual_predictions,
            residual_probabilities,
        ),
        "baseline_expectation": {
            "training_candidates": baseline_model.get("training_candidates"),
            "min_cell_rows": baseline_model.get("min_cell_rows"),
            "overall_expected_excess_percent": baseline_model.get("overall"),
        },
        "_test_rows": test,
        "_residual_predictions": residual_predictions,
        "_residual_probabilities": residual_probabilities,
        "_selection": selection,
        "_baseline": baseline,
    }


def validate_residual_stability_v41(
    *,
    limit: int = DEFAULT_SYMBOL_LIMIT,
    folds: int = OUTER_FOLDS,
) -> dict[str, Any]:
    if not xgboost_available():
        return {"status": "xgboost_unavailable", "model_version": V41_MODEL_VERSION}

    pooled, universe = _build_pooled_rows(limit=limit)
    attach_exact_regime_context(pooled)
    investable, investability = prepare_predictable_investable_rows(pooled)
    fold_specs = expanding_nested_folds(investable, folds=folds)
    if not fold_specs:
        return {
            "status": "insufficient_nested_history",
            "model_version": V41_MODEL_VERSION,
            "pooled_rows": len(pooled),
            "investability": investability,
            "universe": universe,
        }

    raw_fold_results: list[dict[str, Any]] = []
    for fold in fold_specs:
        logger.info(
            "V4.1 stability fold %s: train=%d calibration=%d test=%d",
            fold["fold"],
            len(fold["train"]),
            len(fold["calibration"]),
            len(fold["test"]),
        )
        result = _fit_fold(fold)
        if result is not None:
            raw_fold_results.append(result)

    if not raw_fold_results:
        return {
            "status": "no_valid_folds",
            "model_version": V41_MODEL_VERSION,
            "requested_folds": len(fold_specs),
            "investability": investability,
            "universe": universe,
        }

    fold_summaries: list[dict[str, Any]] = []
    all_test_rows: list[dict[str, Any]] = []
    all_residual_predictions: list[float] = []
    all_residual_probabilities: list[float] = []
    combined_selection = {"selected": [], "days": []}
    combined_baseline = {"selected": [], "days": []}

    for result in raw_fold_results:
        all_test_rows.extend(result.pop("_test_rows"))
        all_residual_predictions.extend(result.pop("_residual_predictions"))
        all_residual_probabilities.extend(result.pop("_residual_probabilities"))
        selection = result.pop("_selection")
        baseline = result.pop("_baseline")
        combined_selection["selected"].extend(selection.get("selected", []))
        combined_selection["days"].extend(selection.get("days", []))
        combined_baseline["selected"].extend(baseline.get("selected", []))
        combined_baseline["days"].extend(baseline.get("days", []))
        fold_summaries.append(result)

    v41_metrics = evaluate_selection(combined_selection)
    baseline_metrics = evaluate_selection(combined_baseline)
    incremental = compare_same_breadth(combined_selection, combined_baseline)
    non_overlap = non_overlapping_incremental_portfolio(combined_selection, combined_baseline)
    override_summary = override_diagnostics(combined_selection)
    aggregate_breakdowns = stability_breakdowns(combined_selection)
    residual_buckets = calibration_diagnostics(
        all_test_rows,
        all_residual_predictions,
        all_residual_probabilities,
    )

    positive_folds = sum(
        1
        for fold in fold_summaries
        if (fold.get("v41", {}).get("mean_net_excess_percent") or 0.0) > 0.0
    )
    baseline_beating_folds = sum(
        1
        for fold in fold_summaries
        if (fold.get("incremental", {}).get("mean_incremental_net_excess_percent") or 0.0) > 0.0
    )

    selected_risk = v41_metrics.get("severe_drawdown_rate")
    baseline_risk = baseline_metrics.get("severe_drawdown_rate")
    relative_risk_reduction = (
        1.0 - float(selected_risk) / float(baseline_risk)
        if selected_risk is not None and baseline_risk not in (None, 0.0)
        else None
    )
    selected_mae = v41_metrics.get("mean_mae_percent")
    baseline_mae = baseline_metrics.get("mean_mae_percent")
    mean_mae_reduction = (
        1.0 - float(selected_mae) / float(baseline_mae)
        if selected_mae is not None and baseline_mae not in (None, 0.0)
        else None
    )

    incremental_bootstrap_low = incremental.get("incremental_bootstrap_95", {}).get("low")
    non_overlap_bootstrap_low = non_overlap.get("incremental_bootstrap_95", {}).get("low")
    checks = {
        "enough_valid_outer_folds": len(fold_summaries) >= MIN_VALID_FOLDS,
        "positive_after_1pct_in_at_least_3_folds": positive_folds >= MIN_POSITIVE_FOLDS,
        "beats_exact_same_breadth_baseline_in_at_least_3_folds": baseline_beating_folds >= MIN_BASELINE_BEATING_FOLDS,
        "aggregate_mean_incremental_alpha_positive": (
            incremental.get("mean_incremental_net_excess_percent") is not None
            and float(incremental["mean_incremental_net_excess_percent"]) > 0.0
        ),
        "aggregate_median_incremental_alpha_positive": (
            incremental.get("median_incremental_net_excess_percent") is not None
            and float(incremental["median_incremental_net_excess_percent"]) > 0.0
        ),
        "aggregate_selected_median_net_excess_positive": (
            v41_metrics.get("median_net_excess_percent") is not None
            and float(v41_metrics["median_net_excess_percent"]) > 0.0
        ),
        "risk_reduction_vs_same_breadth_baseline_at_least_10pct": (
            relative_risk_reduction is not None
            and float(relative_risk_reduction) >= MIN_RISK_REDUCTION_VS_BASELINE
        ),
        "enough_selected_rows": int(v41_metrics.get("selected_rows") or 0) >= MIN_SELECTED_ROWS,
        "enough_active_dates": int(v41_metrics.get("active_dates") or 0) >= MIN_ACTIVE_DATES,
        "same_breadth_incremental_bootstrap_lower_bound_positive": (
            incremental_bootstrap_low is not None and float(incremental_bootstrap_low) > 0.0
        ),
        "non_overlap_incremental_bootstrap_lower_bound_positive": (
            non_overlap_bootstrap_low is not None and float(non_overlap_bootstrap_low) > 0.0
        ),
    }
    passed = all(checks.values())

    return {
        "status": "ready",
        "model_version": V41_MODEL_VERSION,
        "policy_version": V41_POLICY_VERSION,
        "development_verdict": "candidate_for_forward_shadow" if passed else "continue_research",
        "target": {
            "architecture": "V4 candidate-first residual alpha with conservative bounded ML overrides",
            "residual_calibration": "calibration-only monotonic PAVA mapping",
            "risk": "continuous MAE/MFE with bounded risk penalty and hard extreme-risk rejection",
            "cost_hurdle_percent": V4_EXECUTION_HURDLE_PERCENT,
            "gate_changed_from_v4": False,
        },
        "pooled_rows": len(pooled),
        "investable_rows": len(investable),
        "valid_outer_folds": len(fold_summaries),
        "investability": investability,
        "summary": {
            "positive_after_1pct_folds": positive_folds,
            "beats_exact_same_breadth_baseline_folds": baseline_beating_folds,
            "selected_rows": v41_metrics.get("selected_rows"),
            "active_dates": v41_metrics.get("active_dates"),
            "precision_after_1pct": v41_metrics.get("precision_after_1pct"),
            "mean_net_excess_percent": v41_metrics.get("mean_net_excess_percent"),
            "median_net_excess_percent": v41_metrics.get("median_net_excess_percent"),
            "baseline_mean_net_excess_percent": baseline_metrics.get("mean_net_excess_percent"),
            "baseline_median_net_excess_percent": baseline_metrics.get("median_net_excess_percent"),
            "mean_incremental_alpha_percent": incremental.get("mean_incremental_net_excess_percent"),
            "median_incremental_alpha_percent": incremental.get("median_incremental_net_excess_percent"),
            "incremental_bootstrap_95": incremental.get("incremental_bootstrap_95"),
            "selected_severe_drawdown_rate": selected_risk,
            "baseline_severe_drawdown_rate": baseline_risk,
            "relative_risk_reduction_vs_baseline": relative_risk_reduction,
            "selected_mean_mae_percent": selected_mae,
            "baseline_mean_mae_percent": baseline_mae,
            "mean_mae_reduction_vs_baseline": mean_mae_reduction,
            "selected_mean_mfe_percent": v41_metrics.get("mean_mfe_percent"),
            "baseline_mean_mfe_percent": baseline_metrics.get("mean_mfe_percent"),
            "non_overlap_cohorts": non_overlap.get("cohorts"),
            "non_overlap_mean_incremental_alpha_percent": non_overlap.get("mean_incremental_net_excess_percent"),
            "non_overlap_incremental_bootstrap_95": non_overlap.get("incremental_bootstrap_95"),
            "v41_approx_cagr_percent": non_overlap.get("v4_approx_cagr_percent"),
            "baseline_approx_cagr_percent": non_overlap.get("baseline_approx_cagr_percent"),
            "nepse_approx_cagr_percent": non_overlap.get("nepse_approx_cagr_percent"),
        },
        "v41_selection": v41_metrics,
        "matched_baseline": baseline_metrics,
        "incremental_alpha": incremental,
        "non_overlapping_incremental": non_overlap,
        "override_diagnostics": override_summary,
        "residual_calibration_buckets": residual_buckets,
        "stability_breakdowns": aggregate_breakdowns,
        "fold_consistency": {
            "positive_after_1pct_folds": positive_folds,
            "beats_exact_same_breadth_baseline_folds": baseline_beating_folds,
            "folds": fold_summaries,
        },
        "research_gate": {"status": "pass" if passed else "review", "checks": checks},
        "universe": universe,
        "interpretation": {
            "fresh_untouched_historical_holdout": False,
            "development_evidence_only": True,
            "v4_architecture_frozen": True,
            "gate_relaxed": False,
            "candidate_first_training": True,
            "training_only_baseline_expectation": True,
            "calibration_only_monotonic_residual_map": True,
            "bounded_ml_override": True,
            "final_confirmation_required": "separate future V4.1 shadow ledger before any live-model promotion",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Nested V4.1 residual stability challenger for ArthaSignal")
    parser.add_argument("--limit", type=int, default=DEFAULT_SYMBOL_LIMIT)
    parser.add_argument("--folds", type=int, default=OUTER_FOLDS)
    args = parser.parse_args()
    print(json.dumps(validate_residual_stability_v41(limit=args.limit, folds=args.folds), indent=2, default=str))


if __name__ == "__main__":
    main()
