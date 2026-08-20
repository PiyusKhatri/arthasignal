from __future__ import annotations

from src.services.quant_residual_alpha import v4_feature_vector


def _row() -> dict:
    return {
        "features": {
            "return_5d": 2.0,
            "return_20d": 5.0,
            "return_60d": 12.0,
            "annualized_volatility_20d": 20.0,
            "drawdown_60d": -4.0,
            "turnover_ratio_20d": 1.4,
            "volume_ratio_20d": 1.3,
            "distance_sma50_percent": 4.0,
            "relative_strength_market_20d": 3.0,
            "relative_strength_sector_20d": 2.0,
        },
        "baseline_score": 1.5,
        "baseline_percentile": 0.9,
        "baseline_expected_excess_percent": 2.0,
        "v4_sector_regime": "leading",
        "liquidity_bucket": "high",
        "regime_context": {
            "market_return_20d_percent": 4.0,
            "market_return_60d_percent": 10.0,
            "market_drawdown_252d_percent": -3.0,
            "market_annualized_volatility_60d_percent": 21.0,
            "sector_relative_strength_20d_percent": 3.0,
        },
    }


def test_future_horizon_slippage_cannot_change_v4_features() -> None:
    row = _row()
    without_future_execution_fact = v4_feature_vector(row)
    row["horizon_slippage_sessions"] = 999
    with_future_execution_fact = v4_feature_vector(row)
    assert without_future_execution_fact == with_future_execution_fact
