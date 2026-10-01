from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from src.api.main import app
from src.services.stock_intelligence import (
    _backtest_summary,
    _build_payload,
    _classify_market_regime,
    _confidence_level,
    _forward_validation_for_signals,
    _historical_evidence,
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


def test_intelligence_quality_bands_require_evidence_for_strong_labels() -> None:
    assert _rating(85, confidence_score=75, has_signal=True) == "strong_setup"
    assert _rating(85, confidence_score=60, has_signal=True) == "positive_setup"
    assert _rating(85, confidence_score=80, has_signal=False) == "neutral"
    assert _rating(70, confidence_score=50, has_signal=True) == "positive_setup"
    assert _rating(50, confidence_score=0, has_signal=False) == "neutral"
    assert _rating(49, confidence_score=100, has_signal=True) == "weak"

    assert _confidence_level(70) == "high"
    assert _confidence_level(45) == "medium"
    assert _confidence_level(44) == "low"

    assert _trend_strength(16) == "strong"
    assert _trend_strength(9) == "moderate"
    assert _trend_strength(0) == "weak"

    assert _signal_quality(22, {"signal-a"}) == "high"
    assert _signal_quality(8, {"signal-a"}) == "medium"
    assert _signal_quality(30, set()) == "low"


def test_liquidity_scoring_supports_current_and_legacy_tiers() -> None:
    assert _liquidity_score("high_liquidity") == 10
    assert _liquidity_score("medium_liquidity") == 7
    assert _liquidity_score("low_liquidity") == 3
    assert _liquidity_score("tier_a") == 10
    assert _liquidity_score("tier_b") == 7
    assert _liquidity_score(None) == 0


def test_backtest_summary_exposes_horizon_and_return() -> None:
    rows = [
        SimpleNamespace(
            signal_name="rsi_oversold",
            forward_days=20,
            win_rate=61.0,
            sample_size=120,
            mean_return=4.3,
        )
    ]
    assert _backtest_summary(rows) == [
        {
            "signal_name": "rsi_oversold",
            "forward_days": 20,
            "win_rate": 61.0,
            "sample_size": 120,
            "average_return": 4.3,
        }
    ]


def test_signal_direction_matches_long_only_validation_semantics() -> None:
    assert _signal_direction("rsi_14 < 30 (oversold)") == "bullish"
    assert _signal_direction("close < bollinger_lower") == "bullish"
    assert _signal_direction("rsi_14 > 70 (overbought)") == "neutral"
    assert _signal_direction("doji") == "neutral"
    assert _signal_direction("unknown signal") == "neutral"


def test_valuation_scoring_is_conservative_when_data_missing() -> None:
    from src.services.stock_intelligence import _valuation_score

    score_none, insights = _valuation_score(None)
    assert score_none == 5
    assert any("neutral baseline" in item for item in insights)

    good_fund = SimpleNamespace(pe_ratio=15.0, eps=30.0, pb_ratio=2.0)
    score_good, insights = _valuation_score(good_fund)
    assert score_good == 10
    assert any("EPS is strongly positive" in item for item in insights)
    assert any("not elevated on an absolute basis" in item for item in insights)


def test_historical_evidence_uses_backtest_sample_size_and_return_only() -> None:
    signal = "rsi_14 < 30 (oversold)"
    backtests = [
        SimpleNamespace(
            signal_name=signal,
            forward_days=20,
            win_rate=62.0,
            sample_size=900,
            mean_return=3.2,
        ),
        SimpleNamespace(
            signal_name=signal,
            forward_days=5,
            win_rate=58.0,
            sample_size=950,
            mean_return=1.1,
        ),
    ]

    evidence = _historical_evidence(backtests, {signal})
    assert evidence["scope"] == "market_wide_signal_backtest"
    assert evidence["signals_covered"] == 1
    assert evidence["min_sample_size"] == 900
    assert evidence["weighted_win_rate"] == 0.62
    assert evidence["weighted_average_return"] == 3.2
    assert evidence["average_edge_vs_baseline"] is None
    assert evidence["score"] == 9
    assert evidence["confidence_score"] == 75


def _payload_with_confidence(confidence_rows):
    technical = SimpleNamespace(
        date=date(2026, 8, 20),
        sma_50=500.0,
        sma_200=450.0,
        rsi_14=25.0,
        macd_line=10.0,
        macd_signal=8.0,
    )
    signal = "rsi_14 < 30 (oversold)"
    return _build_payload(
        symbol="TEST",
        company_name="Test Corp",
        sector="Commercial Banks",
        technical=technical,
        fundamental=None,
        price=550.0,
        liquidity="high_liquidity",
        calls=[],
        active_signal_names={signal},
        confidence_rows=confidence_rows,
        backtests=[
            SimpleNamespace(signal_name=signal, forward_days=20, win_rate=62.0, sample_size=900, mean_return=3.2)
        ],
        market_regime=_classify_market_regime([100.0 + i for i in range(260)]),
        validation_status={"policy_version": "test", "signals": {}},
        include_ai=False,
    )


def test_signal_confidence_tiers_do_not_change_any_score_or_label() -> None:
    signal = "rsi_14 < 30 (oversold)"

    def confidence(tier, edge, samples):
        return SimpleNamespace(
            signal_name=signal,
            tier=tier,
            avg_win_rate_minus_baseline=edge,
            min_sample_size=samples,
            recommended_holding_period="20d",
            cost_viability_note=None,
        )

    high = _payload_with_confidence([confidence("high_confidence", 9.0, 5000)])
    weak = _payload_with_confidence([confidence("weak_or_no_edge", -4.0, 10)])
    none = _payload_with_confidence([])

    for key in ("artha_score", "rating", "confidence_score", "confidence_level", "signal_quality", "scores"):
        assert high[key] == weak[key] == none[key]
    assert high["evidence"] == weak["evidence"] == none["evidence"]
    for item in high["confidence"] + weak["confidence"]:
        assert item["tier"] == "under_validation"
        assert "backtest_tier" not in item
        assert "high_confidence" not in str(item)


def test_market_regime_classification_uses_index_history_not_stock_scores() -> None:
    bullish = _classify_market_regime([100.0 + i for i in range(260)], as_of_date="2026-08-20")
    assert bullish["state"] == "bullish"
    assert bullish["score_adjustment"] > 0
    assert bullish["confidence"] >= 80

    bearish = _classify_market_regime([400.0 - i for i in range(260)], as_of_date="2026-08-20")
    assert bearish["state"] == "bearish"
    assert bearish["score_adjustment"] < 0
    assert bearish["risk_level"] in {"medium", "high"}


def test_forward_validation_caps_confidence_until_gate_passes() -> None:
    signal = "rsi_14 < 30 (oversold)"
    collecting_status = {
        "policy_version": "test",
        "signals": {
            signal: {
                "gate_status": "collecting",
                "graded_calls": 10,
                "min_graded_calls": 60,
                "independent_entry_days": 5,
                "min_independent_entry_days": 20,
            }
        },
    }
    collecting = _forward_validation_for_signals(collecting_status, {signal})
    assert collecting["state"] == "collecting"
    assert collecting["confidence_cap"] == 69

    passed_status = {
        "policy_version": "test",
        "signals": {
            signal: {
                "gate_status": "pass",
                "graded_calls": 80,
                "min_graded_calls": 60,
                "independent_entry_days": 25,
                "min_independent_entry_days": 20,
            }
        },
    }
    passed = _forward_validation_for_signals(passed_status, {signal})
    assert passed["state"] == "pass"
    assert passed["confidence_cap"] == 100


def test_payload_without_active_validated_signal_cannot_be_strong_setup() -> None:
    technical = SimpleNamespace(
        date=date(2026, 8, 20),
        sma_50=500.0,
        sma_200=450.0,
        rsi_14=55.0,
        macd_line=10.0,
        macd_signal=8.0,
    )
    payload = _build_payload(
        symbol="TEST",
        company_name="Test Corp",
        sector="Commercial Banks",
        technical=technical,
        fundamental=SimpleNamespace(pe_ratio=15.0, eps=30.0, pb_ratio=2.0, book_value=200.0),
        price=550.0,
        liquidity="high_liquidity",
        calls=[],
        confidence_rows=[],
        backtests=[],
        market_regime=_classify_market_regime([100.0 + i for i in range(260)]),
        validation_status={"policy_version": "test", "signals": {}},
        include_ai=False,
    )
    assert payload["rating"] != "strong_setup"
    assert payload["confidence_score"] <= 44
    assert payload["scores"]["reliability"] == 0
    assert payload["ai_analysis"] is None


def test_ai_analyst_commentary_is_evidence_bounded() -> None:
    from src.services.ai_analyst import _rule_based_fallback

    fallback = _rule_based_fallback(
        symbol="TEST",
        company_name="Test Corp",
        artha_score=78,
        rating="positive_setup",
        strengths=["Price is above its 200-day SMA."],
        warnings=[],
        backtests=[],
        technical={"latest_price": 500, "sma_50": 480, "sma_200": 450},
        confidence_score=64,
        evidence={
            "scope": "market_wide_signal_backtest",
            "min_sample_size": 800,
            "average_edge_vs_baseline": 4.0,
            "weighted_average_return": 3.2,
        },
        market_regime={"state": "bullish"},
        forward_validation={"state": "collecting"},
    )
    assert "summary" in fallback
    assert "Test Corp (TEST)" in fallback["summary"]
    assert "Market-wide signal evidence" in fallback["summary"]
    assert "Forward paper-trade validation is still collecting" in fallback["summary"]
    assert "similar setups in TEST" not in fallback["summary"]
    assert "buy" not in fallback["summary"].lower()
    assert "sell" not in fallback["summary"].lower()
