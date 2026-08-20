from __future__ import annotations

from src.api.main import app
from src.database.models import Base
from src.database.quant_models import QuantModelSnapshot, QuantRobustnessRun, QuantShadowSignal


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
