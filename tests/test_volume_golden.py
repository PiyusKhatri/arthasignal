from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from src.pipeline.indicators import obv, vwap

TEN_DP = Decimal("0.0000000001")


def q(value):
    return value.quantize(TEN_DP, rounding=ROUND_HALF_UP)


def to_decimals(values):
    return [Decimal(v) for v in values]


def test_obv_starts_at_zero_and_handles_up_down_flat():
    closes = to_decimals([100, 105, 105, 110, 108, 108, 112, 112, 109, 115])
    volumes = to_decimals([1000, 500, 300, 700, 200, 400, 600, 150, 350, 800])

    result = obv(closes, volumes)

    assert result[0] == Decimal(0)
    assert result[2] == Decimal(500)
    assert result[3] == Decimal(1200)
    assert result[9] == Decimal(2050)


def test_obv_flat_day_leaves_value_unchanged_from_previous():
    closes = to_decimals([100, 105, 105, 110, 108, 108, 112, 112, 109, 115])
    volumes = to_decimals([1000, 500, 300, 700, 200, 400, 600, 150, 350, 800])

    result = obv(closes, volumes)

    assert closes[2] == closes[1]
    assert result[2] == result[1]
    assert closes[5] == closes[4]
    assert result[5] == result[4]
    assert closes[7] == closes[6]
    assert result[7] == result[6]


def test_vwap_rolling_period_5():
    typical_prices = [100, 102, 98, 104, 100, 117]
    highs = to_decimals([tp + 2 for tp in typical_prices])
    lows = to_decimals([tp - 2 for tp in typical_prices])
    closes = to_decimals(typical_prices)
    volumes = to_decimals([200, 300, 500, 400, 600, 300])

    result = vwap(highs, lows, closes, volumes, 5)

    assert result[0] is None
    assert result[1] is None
    assert result[2] is None
    assert result[3] is None
    assert q(result[4]) == Decimal("100.6000000000")
    assert q(result[5]) == Decimal("103.0000000000")
