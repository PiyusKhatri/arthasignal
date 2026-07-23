from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from src.pipeline.indicators import atr, bollinger_bands

TEN_DP = Decimal("0.0000000001")


def q(value):
    return value.quantize(TEN_DP, rounding=ROUND_HALF_UP)


def to_decimals(values):
    return [Decimal(v) for v in values]


def test_bollinger_bands_period_20_population_stddev():
    prices = to_decimals(
        [110] * 5 + [90, 90] + [110] * 13 + [90, 90] + [110] * 3
    )
    assert len(prices) == 25

    middle, upper, lower = bollinger_bands(prices, 20, 2)

    assert middle[18] is None
    assert upper[18] is None
    assert lower[18] is None

    assert q(middle[19]) == Decimal("108.0000000000")
    assert q(upper[19]) == Decimal("120.0000000000")
    assert q(lower[19]) == Decimal("96.0000000000")

    assert q(middle[24]) == Decimal("106.0000000000")
    assert q(upper[24]) == Decimal("122.0000000000")
    assert q(lower[24]) == Decimal("90.0000000000")


def test_bollinger_bands_uses_population_not_sample_stddev():
    prices = to_decimals([110] * 5 + [90, 90] + [110] * 13)
    window = prices[0:20]
    mean = sum(window) / Decimal(20)

    population_variance = sum((v - mean) ** 2 for v in window) / Decimal(20)
    sample_variance = sum((v - mean) ** 2 for v in window) / Decimal(19)

    population_stddev = population_variance.sqrt()
    sample_stddev = sample_variance.sqrt()

    assert population_stddev != sample_stddev
    assert population_stddev == Decimal(6)

    middle, upper, lower = bollinger_bands(prices, 20, 2)

    assert q(upper[19]) == q(mean + 2 * population_stddev)
    assert q(upper[19]) != q(mean + 2 * sample_stddev)


def test_atr_period_14_wilder_seed_and_recursion():
    highs = to_decimals([110] * 15 + [129, 152, 157])
    lows = to_decimals([100] * 15 + [126, 150, 145])
    closes = to_decimals([105] * 15 + [127, 151, 151])

    result = atr(highs, lows, closes, 14)

    assert result[13] is None
    assert q(result[14]) == Decimal("10.0000000000")
    assert q(result[15]) == Decimal("11.0000000000")
    assert q(result[16]) == Decimal("12.0000000000")
    assert q(result[17]) == Decimal("12.0000000000")


def test_atr_true_range_driven_by_prev_close_gap_not_high_low():
    highs = to_decimals([110] * 15 + [129, 152, 157])
    lows = to_decimals([100] * 15 + [126, 150, 145])
    closes = to_decimals([105] * 15 + [127, 151, 151])

    high_minus_low_15 = highs[15] - lows[15]
    gap_term_15 = abs(highs[15] - closes[14])
    assert gap_term_15 > high_minus_low_15

    result = atr(highs, lows, closes, 14)
    assert q(result[15]) == Decimal("11.0000000000")


def test_atr_period_14_naive_average_would_diverge_from_wilder():
    highs = to_decimals([110] * 15 + [129, 152, 157])
    lows = to_decimals([100] * 15 + [126, 150, 145])
    closes = to_decimals([105] * 15 + [127, 151, 151])

    true_ranges = []
    for i in range(1, len(highs)):
        h = highs[i]
        l = lows[i]
        pc = closes[i - 1]
        true_ranges.append(max(h - l, abs(h - pc), abs(l - pc)))

    naive_atr_16 = sum(true_ranges[2:16]) / Decimal(14)

    result = atr(highs, lows, closes, 14)

    assert naive_atr_16 != result[16]
    assert q(result[16]) == Decimal("12.0000000000")
