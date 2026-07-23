from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.pipeline.multitimeframe import resample_ohlcv

WEEKLY_BARS = [
    {"date": date(2026, 4, 5), "open": Decimal(100), "high": Decimal(105), "low": Decimal(99), "close": Decimal(103), "volume": 10000},
    {"date": date(2026, 4, 6), "open": Decimal(103), "high": Decimal(108), "low": Decimal(102), "close": Decimal(107), "volume": 12000},
    {"date": date(2026, 4, 7), "open": Decimal(107), "high": Decimal(110), "low": Decimal(104), "close": Decimal(106), "volume": 9000},
    {"date": date(2026, 4, 8), "open": Decimal(106), "high": Decimal(109), "low": Decimal(101), "close": Decimal(108), "volume": 15000},
    {"date": date(2026, 4, 9), "open": Decimal(108), "high": Decimal(112), "low": Decimal(107), "close": Decimal(111), "volume": 11000},
    {"date": date(2026, 4, 13), "open": Decimal(111), "high": Decimal(115), "low": Decimal(110), "close": Decimal(113), "volume": 8000},
    {"date": date(2026, 4, 14), "open": Decimal(113), "high": Decimal(114), "low": Decimal(108), "close": Decimal(109), "volume": 13000},
    {"date": date(2026, 4, 15), "open": Decimal(109), "high": Decimal(116), "low": Decimal(109), "close": Decimal(115), "volume": 17000},
    {"date": date(2026, 4, 16), "open": Decimal(115), "high": Decimal(117), "low": Decimal(112), "close": Decimal(114), "volume": 10000},
    {"date": date(2026, 4, 17), "open": Decimal(114), "high": Decimal(118), "low": Decimal(113), "close": Decimal(116), "volume": 9500},
    {"date": date(2026, 4, 20), "open": Decimal(116), "high": Decimal(119), "low": Decimal(115), "close": Decimal(118), "volume": 14000},
    {"date": date(2026, 4, 21), "open": Decimal(118), "high": Decimal(120), "low": Decimal(116), "close": Decimal(117), "volume": 16000},
    {"date": date(2026, 4, 22), "open": Decimal(117), "high": Decimal(121), "low": Decimal(115), "close": Decimal(120), "volume": 12000},
    {"date": date(2026, 4, 23), "open": Decimal(120), "high": Decimal(123), "low": Decimal(118), "close": Decimal(119), "volume": 11000},
    {"date": date(2026, 4, 24), "open": Decimal(119), "high": Decimal(122), "low": Decimal(117), "close": Decimal(121), "volume": 13500},
]

MONTHLY_BARS = [
    {"date": date(2026, 4, 28), "open": Decimal(200), "high": Decimal(205), "low": Decimal(198), "close": Decimal(203), "volume": 5000},
    {"date": date(2026, 4, 29), "open": Decimal(203), "high": Decimal(208), "low": Decimal(201), "close": Decimal(206), "volume": 6000},
    {"date": date(2026, 4, 30), "open": Decimal(206), "high": Decimal(210), "low": Decimal(204), "close": Decimal(207), "volume": 7000},
    {"date": date(2026, 5, 1), "open": Decimal(207), "high": Decimal(211), "low": Decimal(205), "close": Decimal(209), "volume": 4000},
    {"date": date(2026, 5, 4), "open": Decimal(209), "high": Decimal(213), "low": Decimal(206), "close": Decimal(212), "volume": 6500},
    {"date": date(2026, 5, 5), "open": Decimal(212), "high": Decimal(215), "low": Decimal(210), "close": Decimal(214), "volume": 5500},
]


def test_weekly_resample_groups_into_three_iso_weeks_across_convention_shift():
    result = resample_ohlcv(WEEKLY_BARS, "W")

    assert len(result) == 3
    assert result[0]["period_start"] == date(2026, 4, 5)
    assert result[0]["period_end"] == date(2026, 4, 9)
    assert result[1]["period_start"] == date(2026, 4, 13)
    assert result[1]["period_end"] == date(2026, 4, 17)
    assert result[2]["period_start"] == date(2026, 4, 20)
    assert result[2]["period_end"] == date(2026, 4, 24)


def test_weekly_resample_week_one_ohlcv_old_convention():
    result = resample_ohlcv(WEEKLY_BARS, "W")
    week1 = result[0]

    assert week1["open"] == Decimal(100)
    assert week1["high"] == Decimal(112)
    assert week1["low"] == Decimal(99)
    assert week1["close"] == Decimal(111)
    assert week1["volume"] == Decimal(57000)


def test_weekly_resample_week_two_ohlcv_new_convention():
    result = resample_ohlcv(WEEKLY_BARS, "W")
    week2 = result[1]

    assert week2["open"] == Decimal(111)
    assert week2["high"] == Decimal(118)
    assert week2["low"] == Decimal(108)
    assert week2["close"] == Decimal(116)
    assert week2["volume"] == Decimal(57500)


def test_weekly_resample_week_three_ohlcv_new_convention():
    result = resample_ohlcv(WEEKLY_BARS, "W")
    week3 = result[2]

    assert week3["open"] == Decimal(116)
    assert week3["high"] == Decimal(123)
    assert week3["low"] == Decimal(115)
    assert week3["close"] == Decimal(121)
    assert week3["volume"] == Decimal(66500)


def test_monthly_resample_splits_on_calendar_month_boundary():
    result = resample_ohlcv(MONTHLY_BARS, "M")

    assert len(result) == 2
    assert result[0]["period_start"] == date(2026, 4, 28)
    assert result[0]["period_end"] == date(2026, 4, 30)
    assert result[1]["period_start"] == date(2026, 5, 1)
    assert result[1]["period_end"] == date(2026, 5, 5)


def test_monthly_resample_april_bar_ohlcv():
    result = resample_ohlcv(MONTHLY_BARS, "M")
    april_bar = result[0]

    assert april_bar["open"] == Decimal(200)
    assert april_bar["high"] == Decimal(210)
    assert april_bar["low"] == Decimal(198)
    assert april_bar["close"] == Decimal(207)
    assert april_bar["volume"] == Decimal(18000)
