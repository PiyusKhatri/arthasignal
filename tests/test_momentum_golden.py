from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from src.pipeline.indicators import cci, roc, stochastic

TEN_DP = Decimal("0.0000000001")


def q(value):
    return value.quantize(TEN_DP, rounding=ROUND_HALF_UP)


def to_decimals(values):
    return [Decimal(v) for v in values]


def test_stochastic_k_and_d_period_14_3():
    highs = to_decimals([110] * 13 + [120, 115, 118])
    lows = to_decimals([90] * 13 + [95, 92, 100])
    closes = to_decimals([100] * 13 + [111, 105, 99])

    k_line, d_line = stochastic(highs, lows, closes, 14, 3)

    assert k_line[12] is None
    assert q(k_line[13]) == Decimal("70.0000000000")
    assert q(k_line[14]) == Decimal("50.0000000000")
    assert q(k_line[15]) == Decimal("30.0000000000")

    assert d_line[13] is None
    assert d_line[14] is None
    assert q(d_line[15]) == Decimal("50.0000000000")


def test_cci_period_20_mean_deviation_not_standard_deviation():
    typical_prices = [100] * 10 + [105, 95, 110, 90, 115, 85, 120, 80, 125, 75]
    highs = to_decimals([tp + 2 for tp in typical_prices])
    lows = to_decimals([tp - 2 for tp in typical_prices])
    closes = to_decimals(typical_prices)

    manual_tp = [(h + l + c) / Decimal(3) for h, l, c in zip(highs, lows, closes)]
    manual_sma_tp = sum(manual_tp) / Decimal(20)
    manual_mean_deviation = sum(abs(tp - manual_sma_tp) for tp in manual_tp) / Decimal(20)

    assert manual_sma_tp == Decimal(100)
    assert manual_mean_deviation == Decimal("7.5")

    manual_variance = sum((tp - manual_sma_tp) ** 2 for tp in manual_tp) / Decimal(20)
    manual_std_dev = manual_variance.sqrt()
    assert manual_std_dev != manual_mean_deviation

    result = cci(highs, lows, closes, 20)

    assert all(v is None for v in result[0:19])
    assert q(result[19]) == Decimal("-222.2222222222")


def test_roc_period_12():
    closes = to_decimals([100, 110, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 120, 143])
    result = roc(closes, 12)

    assert all(v is None for v in result[0:12])
    assert q(result[12]) == Decimal("20.0000000000")
    assert q(result[13]) == Decimal("30.0000000000")


def test_roc_period_12_zero_denominator_does_not_crash():
    closes = to_decimals([0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60])
    result = roc(closes, 12)

    assert result[12] is None
