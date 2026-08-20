from __future__ import annotations

from datetime import date, timedelta

from src.pipeline.validate_regime_decision_v3 import _index_context, _regime_matched_baseline
from src.services.quant_decision_policy import evaluate_dynamic_selection


def test_index_context_cannot_see_future_values() -> None:
    start = date(2020, 1, 1)
    dates = [start + timedelta(days=index) for index in range(300)]
    history = [1000.0 + index for index in range(300)]
    target = dates[220]

    normal = _index_context({"dates": dates, "closes": history}, target)
    distorted_future = history[:221] + [100000.0 + index for index in range(79)]
    after_future_change = _index_context({"dates": dates, "closes": distorted_future}, target)

    assert normal == after_future_change
    assert normal["close"] == history[220]


def _row(trading_date: date, symbol: str, rank: int) -> dict:
    return {
        "date": trading_date,
        "label_end_date": trading_date + timedelta(days=28),
        "symbol": symbol,
        "sector": "Bank",
        "stock_return_percent": 6.0,
        "market_return_percent": 3.0,
        "excess_return_percent": 3.0,
        "max_adverse_percent": -2.0,
        "regime_context": {
            "market_close": 2200.0,
            "market_sma50": 2100.0,
            "market_sma200": 2000.0,
            "market_return_20d_percent": 3.0,
            "market_return_60d_percent": 8.0,
            "market_drawdown_252d_percent": -5.0,
            "market_annualized_volatility_60d_percent": 20.0,
            "sector_relative_strength_20d_percent": 3.0,
        },
        "features": {
            "return_5d": 1.0,
            "return_20d": 6.0,
            "return_60d": 10.0 + rank,
            "annualized_volatility_20d": 20.0,
            "drawdown_60d": -2.0,
            "turnover_ratio_20d": 1.2,
            "volume_ratio_20d": 1.1,
            "distance_sma50_percent": 4.0,
            "relative_strength_market_20d": 3.0 + rank * 0.1,
            "relative_strength_sector_20d": 1.0,
        },
    }


def test_matched_breadth_baseline_uses_requested_active_breadth() -> None:
    trading_date = date(2024, 1, 1)
    rows = [_row(trading_date, f"S{index:02d}", index) for index in range(12)]
    selection = _regime_matched_baseline(rows, selected_counts={trading_date: 3})
    metrics = evaluate_dynamic_selection(selection, cost_percent=1.0)

    assert len(selection["selected"]) == 3
    assert metrics["selected_rows"] == 3
    assert metrics["active_dates"] == 1
