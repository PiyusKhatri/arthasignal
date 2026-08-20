from __future__ import annotations

from src.api.main import app
from src.database.e1_models import QuantE1ForwardDecision, QuantE1ForwardRun
from src.database.models import Base
from src.database.quant_models import (
    QuantModelSnapshot,
    QuantRobustnessRun,
    QuantShadowSignal,
    QuantV41ModelSnapshot,
    QuantV41ShadowRun,
    QuantV41ShadowSignal,
)


def _collect_paths(obj) -> set[str]:
    paths: set[str] = set()
    path = getattr(obj, "path", None)
    if path:
        paths.add(path)
    for route in getattr(obj, "routes", []):
        paths |= _collect_paths(route)
    original_router = getattr(obj, "original_router", None)
    if original_router is not None:
        paths |= _collect_paths(original_router)
    return paths


def test_quant_research_routes_are_registered() -> None:
    paths = _collect_paths(app)
    assert "/stocks/{symbol}/quant-research" in paths
    assert "/market/quant-regime" in paths
    assert "/quant/validation" in paths


def test_quant_shadow_table_is_registered_in_metadata() -> None:
    assert QuantShadowSignal.__tablename__ == "quant_shadow_signals"
    assert "quant_shadow_signals" in Base.metadata.tables
    table = Base.metadata.tables["quant_shadow_signals"]
    expected = {
        "symbol",
        "as_of_date",
        "horizon_days",
        "feature_version",
        "probability_outperform",
        "confidence_score",
        "realized_excess_return_percent",
        "success_after_cost",
    }
    assert expected.issubset(table.columns.keys())


def test_quant_model_snapshot_table_is_registered_in_metadata() -> None:
    assert QuantModelSnapshot.__tablename__ == "quant_model_snapshots"
    assert "quant_model_snapshots" in Base.metadata.tables
    table = Base.metadata.tables["quant_model_snapshots"]
    expected = {
        "model_version",
        "feature_version",
        "trained_through",
        "horizon_days",
        "training_rows",
        "calibration_rows",
        "test_rows",
        "classifier_blob",
        "ranker_blob",
        "calibration_intercept",
        "calibration_slope",
        "metrics_json",
    }
    assert expected.issubset(table.columns.keys())


def test_quant_robustness_audit_table_is_registered_in_metadata() -> None:
    assert QuantRobustnessRun.__tablename__ == "quant_robustness_runs"
    assert "quant_robustness_runs" in Base.metadata.tables
    table = Base.metadata.tables["quant_robustness_runs"]
    expected = {
        "model_snapshot_id",
        "policy_version",
        "gate_status",
        "report_json",
        "created_at",
    }
    assert expected.issubset(table.columns.keys())


def test_v41_frozen_model_snapshot_is_registered() -> None:
    assert QuantV41ModelSnapshot.__tablename__ == "quant_v41_model_snapshots"
    table = Base.metadata.tables["quant_v41_model_snapshots"]
    expected = {
        "model_version",
        "policy_version",
        "trained_through",
        "residual_regressor_blob",
        "residual_classifier_blob",
        "mae_regressor_blob",
        "mfe_regressor_blob",
        "baseline_expectation_json",
        "residual_calibrator_json",
        "probability_calibrator_json",
        "artifact_fingerprint",
    }
    assert expected.issubset(table.columns.keys())


def test_v41_forward_shadow_table_is_registered() -> None:
    assert QuantV41ShadowSignal.__tablename__ == "quant_v41_shadow_signals"
    table = Base.metadata.tables["quant_v41_shadow_signals"]
    expected = {
        "model_snapshot_id",
        "symbol",
        "as_of_date",
        "prediction_fingerprint",
        "baseline_rank",
        "v41_rank",
        "selected_v41",
        "selected_baseline",
        "override_action",
        "predicted_residual_alpha_percent",
        "probability_positive_residual",
        "predicted_mae_percent",
        "predicted_mfe_percent",
        "entry_price",
        "status",
        "realized_excess_return_percent",
        "realized_mae_percent",
        "realized_mfe_percent",
    }
    assert expected.issubset(table.columns.keys())


def test_v41_shadow_run_heartbeat_table_is_registered() -> None:
    assert QuantV41ShadowRun.__tablename__ == "quant_v41_shadow_runs"
    table = Base.metadata.tables["quant_v41_shadow_runs"]
    expected = {
        "model_snapshot_id",
        "as_of_date",
        "model_version",
        "policy_version",
        "artifact_fingerprint",
        "run_status",
        "symbols_considered",
        "eligible_rows",
        "candidate_rows",
        "v41_selected",
        "baseline_selected",
        "rows_inserted",
        "failure_code",
        "details_json",
        "run_fingerprint",
        "created_at",
    }
    assert expected.issubset(table.columns.keys())


def test_e1_forward_run_table_is_registered() -> None:
    assert QuantE1ForwardRun.__tablename__ == "quant_e1_forward_runs"
    table = Base.metadata.tables["quant_e1_forward_runs"]
    expected = {
        "model_snapshot_id",
        "as_of_date",
        "model_version",
        "predictive_policy_version",
        "execution_policy_version",
        "artifact_fingerprint",
        "source_v41_run_fingerprint",
        "run_status",
        "candidate_rows",
        "decision_rows",
        "failure_code",
        "details_json",
        "run_fingerprint",
        "created_at",
    }
    assert expected.issubset(table.columns.keys())


def test_e1_forward_decision_table_is_registered() -> None:
    assert QuantE1ForwardDecision.__tablename__ == "quant_e1_forward_decisions"
    table = Base.metadata.tables["quant_e1_forward_decisions"]
    expected = {
        "run_id",
        "symbol",
        "as_of_date",
        "source_prediction_fingerprint",
        "baseline_rank",
        "baseline_percentile",
        "baseline_score",
        "final_score",
        "override_action",
        "expected_excess_return_percent",
        "market_regime",
        "sector_regime",
        "liquidity_bucket",
        "entry_eligible_v41",
        "hold_eligible_v41",
        "entry_eligible_baseline",
        "decision_fingerprint",
        "created_at",
    }
    assert expected.issubset(table.columns.keys())
