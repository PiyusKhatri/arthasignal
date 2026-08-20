from __future__ import annotations

from types import SimpleNamespace

from src.api.main import app
from src.services.stock_intelligence import (
    _backtest_summary,
    _confidence_level,
    _confidence_score,
    _liquidity_score,
    _rating,
    _signal_quality,
    _trend_strength,
)


def _collect_paths(obj) -> set[str]:
    """Recursively collect all route paths, including through _IncludedRouter."""
    paths: set[str] = set()
    path = getattr(obj, "path", None)
    if path:
        paths.add(path)
    for route in getattr(obj, "routes", []):
        paths |= _collect_paths(route)
    # FastAPI wraps sub-routers in _IncludedRouter; real routes live in .original_router
    orig = getattr(obj, "original_router", None)
    if orig is not None:
        paths |= _collect_paths(orig)
    return paths


def test_intelligence_routes_are_registered() -> None:
    paths = _collect_paths(app)
    assert "/stocks/{symbol}/intelligence" in paths
    assert "/market/intelligence" in paths


def test_intelligence_quality_bands_are_stable() -> None:
    assert _rating(85) == "strong_setup"
    assert _rating(70) == "positive_setup"
    assert _rating(50) == "neutral"
    assert _rating(49) == "weak"

    assert _confidence_level(20) == "high"
    assert _confidence_level(10) == "medium"
    assert _confidence_level(0) == "low"

    assert _trend_strength(25) == "strong"
    assert _trend_strength(15) == "moderate"
    assert _trend_strength(0) == "weak"

    assert _signal_quality(20, {"signal-a"}) == "high"
    assert _signal_quality(0, {"signal-a"}) == "medium"
    assert _signal_quality(0, set()) == "low"


def test_liquidity_scoring_supports_current_and_legacy_tiers() -> None:
    assert _liquidity_score("high_liquidity") == 15
    assert _liquidity_score("medium_liquidity") == 10
    assert _liquidity_score("low_liquidity") == 5
    assert _liquidity_score("tier_a") == 15
    assert _liquidity_score("tier_b") == 10
    assert _liquidity_score(None) == 0


def test_confidence_score_only_uses_relevant_stock_signals() -> None:
    rows = [
        SimpleNamespace(signal_name="relevant", tier="high_confidence"),
        SimpleNamespace(signal_name="unrelated", tier="high_confidence"),
    ]
    assert _confidence_score(rows, {"relevant"}) == 20
    assert _confidence_score(rows, {"missing"}) == 0


def test_backtest_summary_exposes_horizon_and_return() -> None:
    rows = [
        SimpleNamespace(
            signal_name="rsi_oversold",
            forward_days=20,
            win_rate=0.61,
            sample_size=120,
            mean_return=0.043,
        )
    ]
    assert _backtest_summary(rows) == [
        {
            "signal_name": "rsi_oversold",
            "forward_days": 20,
            "win_rate": 0.61,
            "sample_size": 120,
            "average_return": 0.043,
        }
    ]
