from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.quant_models import QuantModelSnapshot, QuantRobustnessRun
from src.pipeline.train_quant_models import DEFAULT_SYMBOL_LIMIT, _build_pooled_rows, _rank_scores
from src.services.nepse_quant_research import _load_stock_series
from src.services.quant_features import FEATURE_NAMES, FEATURE_SCALES
from src.services.quant_model_store import ARTHA_XGB_MODEL_VERSION
from src.services.quant_robustness import build_robustness_report, top_k_sweep
from src.services.quant_xgboost import (
    calibrate_probability,
    chronological_three_way_split,
    fit_platt_calibrator,
    fit_xgb_classifier,
    fit_xgb_ranker,
    predict_xgb_rows,
    xgboost_available,
)

logger = logging.getLogger(__name__)
ROBUSTNESS_POLICY_VERSION = "2026-08-21-v1"
MIN_HIGH_CONFIDENCE_CALLS = 30
MIN_HIGH_CONFIDENCE_DATES = 10
MIN_NON_OVERLAP_COHORTS = 20
MIN_CLUSTER_BOOTSTRAP_PRECISION_LOW = 0.50
STRESS_COST_PERCENT = 1.00


def _attach_historical_turnover(rows: list[dict[str, Any]]) -> None:
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_symbol[str(row.get("symbol") or "")].append(row)

    with get_session() as session:
        for symbol, symbol_rows in by_symbol.items():
            if not symbol:
                continue
            stock = _load_stock_series(session, symbol)
            turnovers = stock.get("turnovers", [])
            for row in symbol_rows:
                index = int(row.get("index") or 0)
                start = max(0, index - 19)
                window = [float(value or 0.0) for value in turnovers[start : index + 1]]
                positive = [value for value in window if value > 0]
                row["trailing_turnover_20d"] = mean(positive) if positive else 0.0


def _normalized_feature(row: dict[str, Any], name: str) -> float:
    raw = float(row.get("features", {}).get(name, 0.0))
    scale = float(FEATURE_SCALES[name])
    if scale <= 0:
        return raw
    return max(-5.0, min(5.0, raw / scale))


def _fixed_multifactor_score(row: dict[str, Any]) -> float:
    """Predefined challenger; coefficients are not tuned on the holdout period."""
    return (
        1.00 * _normalized_feature(row, "relative_strength_market_20d")
        + 0.75 * _normalized_feature(row, "relative_strength_sector_20d")
        + 0.40 * _normalized_feature(row, "return_60d")
        + 0.30 * _normalized_feature(row, "distance_sma50_percent")
        + 0.20 * _normalized_feature(row, "turnover_ratio_20d")
        - 0.20 * _normalized_feature(row, "annualized_volatility_20d")
    )


def _stronger_baselines(test_rows: list[dict[str, Any]]) -> dict[str, Any]:
    score_sets = {
        "market_relative_momentum": [
            float(row["features"].get("relative_strength_market_20d", 0.0)) for row in test_rows
        ],
        "sector_relative_momentum": [
            float(row["features"].get("relative_strength_sector_20d", 0.0)) for row in test_rows
        ],
        "fixed_multifactor": [_fixed_multifactor_score(row) for row in test_rows],
    }
    return {
        name: top_k_sweep(test_rows, scores, ks=(5, 10, 20))
        for name, scores in score_sets.items()
    }


def _robustness_gate(report: dict[str, Any], baselines: dict[str, Any]) -> dict[str, Any]:
    high = report.get("high_confidence", {})
    top10 = report.get("top_k_sweep", {}).get("p_at_10", {})
    cost_key = f"cost_{STRESS_COST_PERCENT:.2f}pct"
    stress = report.get("cost_stress", {}).get(cost_key, {})
    portfolio = report.get("non_overlapping_top10_portfolio", {})

    best_baseline_p10 = None
    for baseline in baselines.values():
        value = baseline.get("p_at_10", {}).get("precision_after_cost")
        if value is not None:
            best_baseline_p10 = float(value) if best_baseline_p10 is None else max(best_baseline_p10, float(value))

    model_p10 = top10.get("precision_after_cost")
    bootstrap_low = high.get("precision_date_cluster_bootstrap_95", {}).get("low_95")
    portfolio_bootstrap_low = portfolio.get("mean_net_excess_bootstrap_95", {}).get("low")

    checks = {
        "enough_high_confidence_calls": int(high.get("calls") or 0) >= MIN_HIGH_CONFIDENCE_CALLS,
        "enough_high_confidence_dates": int(high.get("independent_entry_dates") or 0) >= MIN_HIGH_CONFIDENCE_DATES,
        "cluster_bootstrap_precision_low_above_50pct": (
            bootstrap_low is not None and float(bootstrap_low) >= MIN_CLUSTER_BOOTSTRAP_PRECISION_LOW
        ),
        "top10_beats_fixed_baseline_family": (
            model_p10 is not None
            and (best_baseline_p10 is None or float(model_p10) > float(best_baseline_p10))
        ),
        "top10_remains_positive_at_1pct_cost": (
            stress.get("rank_top_10_mean_net_excess_percent") is not None
            and float(stress["rank_top_10_mean_net_excess_percent"]) > 0.0
        ),
        "enough_non_overlapping_cohorts": int(portfolio.get("cohorts") or 0) >= MIN_NON_OVERLAP_COHORTS,
        "non_overlap_mean_excess_bootstrap_low_positive": (
            portfolio_bootstrap_low is not None and float(portfolio_bootstrap_low) > 0.0
        ),
    }
    passed = all(checks.values())
    return {
        "policy_version": ROBUSTNESS_POLICY_VERSION,
        "status": "pass" if passed else "review",
        "checks": checks,
        "best_baseline_p10": best_baseline_p10,
        "model_p10": model_p10,
        "note": (
            "This gate is stricter than the historical promotion gate and does not by itself enable public signals. "
            "Forward shadow validation remains mandatory."
        ),
    }


def _persist_report(trained_through, audit: dict[str, Any]) -> dict[str, Any]:
    """Persist a separate immutable audit; never mutate the trained model snapshot."""
    with get_session() as session:
        snapshot = session.execute(
            select(QuantModelSnapshot)
            .where(
                QuantModelSnapshot.model_version == ARTHA_XGB_MODEL_VERSION,
                QuantModelSnapshot.trained_through == trained_through,
            )
            .order_by(QuantModelSnapshot.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if snapshot is None:
            return {"persisted": False, "reason": "matching_model_snapshot_not_found"}

        values = {
            "model_snapshot_id": snapshot.id,
            "policy_version": ROBUSTNESS_POLICY_VERSION,
            "gate_status": str(audit.get("gate", {}).get("status") or "review"),
            "report_json": json.dumps(audit, default=str, sort_keys=True),
            "created_at": datetime.now(timezone.utc).replace(tzinfo=None),
        }
        stmt = pg_insert(QuantRobustnessRun).values(values)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["model_snapshot_id", "policy_version"]
        ).returning(QuantRobustnessRun.id)
        inserted_id = session.execute(stmt).scalar_one_or_none()
        if inserted_id is not None:
            return {"persisted": True, "robustness_run_id": inserted_id, "model_snapshot_id": snapshot.id}

        existing_id = session.execute(
            select(QuantRobustnessRun.id).where(
                QuantRobustnessRun.model_snapshot_id == snapshot.id,
                QuantRobustnessRun.policy_version == ROBUSTNESS_POLICY_VERSION,
            )
        ).scalar_one_or_none()
        return {
            "persisted": False,
            "reason": "immutable_audit_already_exists",
            "robustness_run_id": existing_id,
            "model_snapshot_id": snapshot.id,
        }


def _summary(report: dict[str, Any], gate: dict[str, Any]) -> dict[str, Any]:
    high = report.get("high_confidence", {})
    top_k = report.get("top_k_sweep", {})
    stress = report.get("cost_stress", {}).get("cost_1.00pct", {})
    portfolio = report.get("non_overlapping_top10_portfolio", {})
    return {
        "robustness_status": gate.get("status"),
        "high_confidence_calls": high.get("calls"),
        "high_confidence_independent_dates": high.get("independent_entry_dates"),
        "high_confidence_precision": high.get("precision"),
        "high_confidence_cluster_bootstrap_95": high.get("precision_date_cluster_bootstrap_95"),
        "high_confidence_mean_excess_percent": high.get("mean_excess_return_percent"),
        "p_at_5": top_k.get("p_at_5"),
        "p_at_10": top_k.get("p_at_10"),
        "p_at_20": top_k.get("p_at_20"),
        "top10_at_1pct_cost": stress,
        "non_overlapping_portfolio": {
            "cohorts": portfolio.get("cohorts"),
            "mean_net_excess_return_percent": portfolio.get("mean_net_excess_return_percent"),
            "mean_net_excess_bootstrap_95": portfolio.get("mean_net_excess_bootstrap_95"),
            "portfolio_approx_cagr_percent": portfolio.get("portfolio_approx_cagr_percent"),
            "nepse_approx_cagr_percent": portfolio.get("nepse_approx_cagr_percent"),
            "portfolio_max_drawdown_percent": portfolio.get("portfolio_max_drawdown_percent"),
        },
        "best_stronger_baseline_p10": gate.get("best_baseline_p10"),
        "model_p10": gate.get("model_p10"),
    }


def validate_quant_robustness(*, limit: int = DEFAULT_SYMBOL_LIMIT, persist: bool = False) -> dict[str, Any]:
    if not xgboost_available():
        return {"status": "xgboost_unavailable", "policy_version": ROBUSTNESS_POLICY_VERSION}

    pooled, universe = _build_pooled_rows(limit=limit)
    _attach_historical_turnover(pooled)
    split = chronological_three_way_split(pooled)
    train_rows = split["train"]
    calibration_rows = split["calibration"]
    test_rows = split["test"]
    if not train_rows or not calibration_rows or not test_rows:
        return {
            "status": "insufficient_split",
            "policy_version": ROBUSTNESS_POLICY_VERSION,
            "split_sizes": {name: len(rows) for name, rows in split.items()},
        }

    classifier = fit_xgb_classifier(train_rows)
    ranker = fit_xgb_ranker(train_rows)
    if classifier is None or ranker is None:
        return {"status": "model_training_failed", "policy_version": ROBUSTNESS_POLICY_VERSION}

    calibration_raw = predict_xgb_rows(classifier, calibration_rows)
    calibrator = fit_platt_calibrator(calibration_raw, [bool(row["success"]) for row in calibration_rows])
    test_raw = predict_xgb_rows(classifier, test_rows)
    probabilities = [calibrate_probability(value, calibrator) for value in test_raw]
    if any(value is None for value in probabilities):
        return {"status": "calibration_failed", "policy_version": ROBUSTNESS_POLICY_VERSION}
    calibrated = [float(value) for value in probabilities if value is not None]
    rank_scores = _rank_scores(ranker, test_rows)
    if len(rank_scores) != len(test_rows):
        return {"status": "rank_scoring_failed", "policy_version": ROBUSTNESS_POLICY_VERSION}

    report = build_robustness_report(test_rows, calibrated, rank_scores)
    baselines = _stronger_baselines(test_rows)
    gate = _robustness_gate(report, baselines)
    trained_through = max(row["date"] for row in pooled)
    audit = {
        "policy_version": ROBUSTNESS_POLICY_VERSION,
        "gate": gate,
        "report": report,
        "stronger_baselines": baselines,
    }

    result = {
        "status": "ready",
        "policy_version": ROBUSTNESS_POLICY_VERSION,
        "model_version": ARTHA_XGB_MODEL_VERSION,
        "trained_through": trained_through.isoformat(),
        "split_sizes": {name: len(rows) for name, rows in split.items()},
        "universe": universe,
        "robustness_gate": gate,
        "summary": _summary(report, gate),
        "report": report,
        "stronger_baselines": baselines,
        "feature_scope": {
            "model_features": list(FEATURE_NAMES),
            "point_in_time_liquidity_used_for_diagnostics_only": True,
            "no_retraining_or_threshold_tuning_on_test_results": True,
            "model_snapshot_remains_immutable": True,
        },
    }
    if persist:
        result["persistence"] = _persist_report(trained_through, audit)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run strict robustness diagnostics on the untouched ArthaSignal ML holdout")
    parser.add_argument("--limit", type=int, default=DEFAULT_SYMBOL_LIMIT)
    parser.add_argument(
        "--persist",
        action="store_true",
        help="Create one immutable robustness audit linked to the matching trained model snapshot",
    )
    args = parser.parse_args()
    print(json.dumps(validate_quant_robustness(limit=args.limit, persist=args.persist), indent=2, default=str))


if __name__ == "__main__":
    main()
