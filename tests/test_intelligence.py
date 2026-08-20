from __future__ import annotations

from types import SimpleNamespace

from src.api.main import app
from src.services.stock_intelligence import (
    _backtest_summary,
    _confidence_level,
    _confidence_score,
    _liquidity_score,
    _rating,
    _signal_direction,
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


def test_chart_signal_direction_is_explicit_and_stable() -> None:
    assert _signal_direction("rsi_14 < 30 (oversold)") == "bullish"
    assert _signal_direction("close < bollinger_lower") == "bullish"
    assert _signal_direction("rsi_14 > 70 (overbought)") == "bearish"
    assert _signal_direction("doji") == "neutral"
    assert _signal_direction("unknown signal") == "neutral"


def test_valuation_scoring() -> None:
    from src.services.stock_intelligence import _valuation_score

    score_none, _ = _valuation_score(None)
    assert score_none == 5

    good_fund = SimpleNamespace(pe_ratio=15.0, eps=30.0, pb_ratio=2.0)
    score_good, insights = _valuation_score(good_fund)
    assert score_good == 10
    assert any("Strong EPS" in i for i in insights)
    assert any("Attractive P/E" in i for i in insights)


def test_ai_analyst_commentary() -> None:
    from src.services.ai_analyst import _rule_based_fallback, generate_ai_analyst_commentary

    # 1. Test offline rule-based fallback specifically
    fallback = _rule_based_fallback(
        symbol="TEST",
        company_name="Test Corp",
        artha_score=85,
        rating="strong_setup",
        strengths=["Long-term golden alignment", "High liquidity"],
        warnings=[],
        backtests=[{"signal_name": "rsi_oversold", "forward_days": 10, "win_rate": 0.72}],
        technical={"latest_price": 500, "sma_50": 480, "sma_200": 450},
    )
    assert "summary" in fallback
    assert "Test Corp (TEST)" in fallback["summary"]
    assert "72.0%" in fallback["summary"]
    assert "key_takeaway" in fallback
    assert "confidence_reason" in fallback

    # 2. Test general commentary generator
    res = generate_ai_analyst_commentary(
        symbol="TEST",
        company_name="Test Corp",
        sector="Commercial Banks",
        as_of_date="2026-08-20",
        artha_score=85,
        rating="strong_setup",
        score_breakdown={"trend": 30, "momentum": 20, "liquidity": 15, "reliability": 10, "risk_adjustment": 5, "valuation": 5},
        strengths=["Long-term golden alignment", "High liquidity"],
        warnings=[],
        technical={"latest_price": 500, "sma_50": 480, "sma_200": 450},
        signals=[],
        backtests=[{"signal_name": "rsi_oversold", "forward_days": 10, "win_rate": 0.72}],
    )
    assert "summary" in res
    assert len(res["summary"]) > 10
    assert "key_takeaway" in res
    assert "confidence_reason" in res


