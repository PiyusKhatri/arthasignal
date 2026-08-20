from __future__ import annotations

from src.api.main import app
from src.database.models import Base
from src.database.quant_models import QuantShadowSignal


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
