from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from src.pipeline.indicators import fibonacci_retracement, fifty_two_week_high_low, pivot_points

TEN_DP = Decimal("0.0000000001")


def q(value):
    return value.quantize(TEN_DP, rounding=ROUND_HALF_UP)


def to_decimals(values):
    return [Decimal(v) for v in values]


def test_fifty_two_week_high_low_excludes_bars_once_window_rolls_past_them():
    highs = to_decimals([500] + [110] * 259)
    lows = to_decimals([50] + [95] * 259)
    assert len(highs) == 260

    high_line, low_line = fifty_two_week_high_low(highs, lows, 252)

    assert high_line[250] is None
    assert low_line[250] is None

    assert q(high_line[251]) == Decimal("500.0000000000")
    assert q(low_line[251]) == Decimal("50.0000000000")

    assert q(high_line[252]) == Decimal("110.0000000000")
    assert q(low_line[252]) == Decimal("95.0000000000")


def test_pivot_points_standard_formula_all_seven_levels():
    highs = to_decimals([132, 140])
    lows = to_decimals([119, 125])
    closes = to_decimals([127, 133])

    pivot, r1, r2, r3, s1, s2, s3 = pivot_points(highs, lows, closes)

    assert pivot[0] is None
    assert r1[0] is None
    assert r2[0] is None
    assert r3[0] is None
    assert s1[0] is None
    assert s2[0] is None
    assert s3[0] is None

    assert q(pivot[1]) == Decimal("126.0000000000")
    assert q(r1[1]) == Decimal("133.0000000000")
    assert q(s1[1]) == Decimal("120.0000000000")
    assert q(r2[1]) == Decimal("139.0000000000")
    assert q(s2[1]) == Decimal("113.0000000000")
    assert q(r3[1]) == Decimal("146.0000000000")
    assert q(s3[1]) == Decimal("107.0000000000")


def test_fibonacci_retracement_all_seven_levels():
    highs = to_decimals([600] * 5 + [1100] + [600] * 14)
    lows = to_decimals([590] * 5 + [590] + [590] * 9 + [100] + [590] * 4)
    assert len(highs) == 20
    assert len(lows) == 20

    levels = fibonacci_retracement(highs, lows, 20)

    for level in levels.values():
        assert level[18] is None

    assert q(levels["0.0"][19]) == Decimal("1100.0000000000")
    assert q(levels["23.6"][19]) == Decimal("864.0000000000")
    assert q(levels["38.2"][19]) == Decimal("718.0000000000")
    assert q(levels["50.0"][19]) == Decimal("600.0000000000")
    assert q(levels["61.8"][19]) == Decimal("482.0000000000")
    assert q(levels["78.6"][19]) == Decimal("314.0000000000")
    assert q(levels["100.0"][19]) == Decimal("100.0000000000")
