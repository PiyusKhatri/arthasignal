from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.services.nepse_quant_research import _classify_regime, _decision
from src.services.quant_features import (
    FEATURE_NAMES,
    analyze_historical_analogs,
    build_feature_row,
    build_probability_ensemble,
    fit_ridge_logistic,
    predict_ridge_logistic,
)


def _series(length: int = 180):
    start = date(2025, 1, 1)
    dates = [start + timedelta(days=i) for i in range(length)]
    market = [1000.0 + i * 1.1 for i in range(length)]
    stock = [100.0 + i * 0.22 + (i % 7) * 0.08 for i in range(length)]
    sector = [500.0 + i * 0.7 for i in range(length)]
    volumes = [1000.0 + (i % 9) * 40.0 for i in range(length)]
    turnovers = [stock[i] * volumes[i] for i in range(length)]
    return dates, stock, volumes, turnovers, market, sector


def test_current_feature_row_never_uses_future_prices() -> None:
    dates, stock, volumes, turnovers, market, sector = _series()
    index = 120

    before = build_feature_row(
        dates=dates,
        closes=stock,
        volumes=volumes,
        turnovers=turnovers,
        index=index,
        market_dates=dates,
        market_closes=market,
        sector_dates=dates,
        sector_closes=sector,
    )
    assert before is not None

    future_stock = stock.copy()
    future_market = market.copy()
    future_sector = sector.copy()
    for i in range(index + 1, len(stock)):
        future_stock[i] *= 4.0
        future_market[i] *= 0.4
        future_sector[i] *= 2.5

    after = build_feature_row(
        dates=dates,
        closes=future_stock,
        volumes=volumes,
        turnovers=turnovers,
        index=index,
        market_dates=dates,
        market_closes=future_market,
        sector_dates=dates,
        sector_closes=future_sector,
    )
    assert after == before


def test_historical_analog_probability_is_shrunk_and_bounded() -> None:
    current = {name: 0.0 for name in FEATURE_NAMES}
    rows = []
    for i in range(20):
        features = {name: 0.05 * (i % 3) for name in FEATURE_NAMES}
        rows.append(
            {
                "date": date(2020, 1, 1) + timedelta(days=i),
                "features": features,
                "success": i < 16,
                "excess_return_percent": 3.0 if i < 16 else -2.0,
                "stock_return_percent": 4.0 if i < 16 else -1.0,
                "market_return_percent": 1.0,
                "max_adverse_percent": -2.0,
                "max_favorable_percent": 5.0,
            }
        )

    result = analyze_historical_analogs(current, rows, max_analogs=20)
    probability = result["probability_outperform_after_cost"]
    assert probability is not None
    assert 0.5 < probability < 0.8  # Bayesian shrinkage prevents raw 80% from being copied blindly.
    assert result["confidence_score"] <= 92
    assert result["effective_sample_size"] > 0


def test_ridge_logistic_learns_direction_without_external_ml_dependency() -> None:
    rows = []
    for i in range(120):
        supportive = i % 2 == 0
        features = {name: 0.0 for name in FEATURE_NAMES}
        features["relative_strength_market_20d"] = 8.0 if supportive else -8.0
        features["return_20d"] = 6.0 if supportive else -6.0
        features["distance_sma50_percent"] = 4.0 if supportive else -4.0
        rows.append({"features": features, "success": supportive})

    model = fit_ridge_logistic(rows)
    assert model is not None

    positive = {name: 0.0 for name in FEATURE_NAMES}
    positive.update(
        {
            "relative_strength_market_20d": 8.0,
            "return_20d": 6.0,
            "distance_sma50_percent": 4.0,
        }
    )
    negative = {name: 0.0 for name in FEATURE_NAMES}
    negative.update(
        {
            "relative_strength_market_20d": -8.0,
            "return_20d": -6.0,
            "distance_sma50_percent": -4.0,
        }
    )

    positive_probability = predict_ridge_logistic(model, positive)
    negative_probability = predict_ridge_logistic(model, negative)
    assert positive_probability is not None and negative_probability is not None
    assert positive_probability > negative_probability
    assert 0.0 < positive_probability < 1.0
    assert 0.0 < negative_probability < 1.0


def test_ensemble_penalizes_model_disagreement() -> None:
    aligned = build_probability_ensemble(
        analog_result={"probability_outperform_after_cost": 0.72, "confidence_score": 80},
        logistic_probability=0.70,
        training_rows=500,
    )
    disagreed = build_probability_ensemble(
        analog_result={"probability_outperform_after_cost": 0.72, "confidence_score": 80},
        logistic_probability=0.30,
        training_rows=500,
    )
    assert aligned["confidence_score"] > disagreed["confidence_score"]
    assert 0.02 <= aligned["probability_outperform_after_cost"] <= 0.98


def test_market_regime_distinguishes_strong_bull_and_bear() -> None:
    dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(240)]
    bullish = [1000.0 + i * 4.0 for i in range(240)]
    bearish = [2000.0 - i * 4.0 for i in range(240)]

    bull = _classify_regime(
        dates=dates,
        closes=bullish,
        breadth_above_50=0.75,
        breadth_above_200=0.70,
        turnover_ratio=1.4,
    )
    bear = _classify_regime(
        dates=dates,
        closes=bearish,
        breadth_above_50=0.25,
        breadth_above_200=0.20,
        turnover_ratio=0.8,
    )

    assert bull["state"] == "strong_bull"
    assert bear["state"] in {"bear", "high_stress"}


def test_public_signal_cannot_bypass_forward_validation_gate() -> None:
    common = {
        "ensemble": {"probability_outperform_after_cost": 0.78, "confidence_score": 82},
        "analogs": {"expected_excess_return_percent": 4.2},
        "confluence": {"supportive_dimensions": 8},
        "market_regime": {"state": "bull"},
        "sector_regime": {"state": "bull"},
        "liquidity": {"quality": "high"},
        "event_risk": {"level": "low"},
    }

    collecting = _decision(
        **common,
        validation_gate={"public_high_confidence_enabled": False},
    )
    passed = _decision(
        **common,
        validation_gate={"public_high_confidence_enabled": True},
    )

    assert collecting["research_label"] == "strong_candidate"
    assert collecting["public_label"] == "research_only"
    assert collecting["public_eligible"] is False
    assert passed["public_label"] == "strong_setup"
    assert passed["public_eligible"] is True
