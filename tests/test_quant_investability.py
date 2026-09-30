from __future__ import annotations

from datetime import date, timedelta

from src.services.quant_investability import horizon_slippage_sessions


def test_horizon_slippage_measures_market_sessions_not_calendar_days() -> None:
    start = date(2024, 1, 1)
    market_dates = [start + timedelta(days=index) for index in range(80)]
    entry = market_dates[10]

    assert horizon_slippage_sessions(market_dates, entry, market_dates[30], horizon_days=20) == 0
    assert horizon_slippage_sessions(market_dates, entry, market_dates[33], horizon_days=20) == 3
    assert horizon_slippage_sessions(market_dates, entry, market_dates[36], horizon_days=20) == 6


def test_horizon_slippage_never_uses_dates_after_actual_resolution() -> None:
    start = date(2024, 1, 1)
    market_dates = [start + timedelta(days=index) for index in range(100)]
    entry = market_dates[20]
    actual_end = market_dates[42]
    original = horizon_slippage_sessions(market_dates, entry, actual_end, horizon_days=20)

    extended = market_dates + [start + timedelta(days=500 + index) for index in range(20)]
    assert horizon_slippage_sessions(extended, entry, actual_end, horizon_days=20) == original
