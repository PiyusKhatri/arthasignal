from __future__ import annotations

from decimal import Decimal

from src.pipeline.indicators import (
    doji,
    engulfing,
    hammer_shape,
    harami,
    marubozu,
    morning_evening_star,
    piercing_dark_cloud_cover,
    shooting_star_shape,
    spinning_top,
    three_soldiers_crows,
    tweezer,
)


def to_decimals(values):
    return [Decimal(str(v)) for v in values]


def ohlc(rows):
    opens = to_decimals([r[0] for r in rows])
    highs = to_decimals([r[1] for r in rows])
    lows = to_decimals([r[2] for r in rows])
    closes = to_decimals([r[3] for r in rows])
    return opens, highs, lows, closes


def test_doji_clean_positive_boundary_and_negative():
    o, h, l, c = ohlc([
        (100, 105, 95, 100.2),
        (100, 105, 95, 100.5),
        (100, 105, 95, 100.6),
    ])
    result = doji(o, h, l, c)
    assert result[0] is True
    assert result[1] is True
    assert result[2] is False


def test_marubozu_clean_positive_boundary_and_negative():
    o, h, l, c = ohlc([
        (100, 110, 100, 110),
        (100.5, 110, 100, 109.5),
        (100.6, 110, 100, 109.4),
        (110, 110, 100, 100),
    ])
    bullish, bearish = marubozu(o, h, l, c)
    assert bullish[0] is True
    assert bullish[1] is True
    assert bullish[2] is False
    assert bearish[3] is True
    assert bullish[3] is False


def test_hammer_shape_positive_and_negatives():
    o, h, l, c = ohlc([
        (100, 102, 94, 102),
        (100, 104, 94, 102),
        (100, 102, 97, 102),
    ])
    result = hammer_shape(o, h, l, c)
    assert result[0] is True
    assert result[1] is False
    assert result[2] is False


def test_shooting_star_shape_positive_and_negative():
    o, h, l, c = ohlc([
        (102, 108, 100, 100),
        (102, 108, 97, 100),
    ])
    result = shooting_star_shape(o, h, l, c)
    assert result[0] is True
    assert result[1] is False


def test_spinning_top_positive_and_negative():
    o, h, l, c = ohlc([
        (100, 107, 96, 103),
        (100, 111, 98, 103),
    ])
    result = spinning_top(o, h, l, c)
    assert result[0] is True
    assert result[1] is False


def test_bullish_engulfing_positive_and_boundary_negative():
    o, h, l, c = ohlc([
        (110, 111, 99, 100),
        (99, 113, 98, 112),
    ])
    bullish, _ = engulfing(o, h, l, c)
    assert bullish[1] is True

    o2, h2, l2, c2 = ohlc([
        (110, 111, 99, 100),
        (100, 113, 98, 112),
    ])
    bullish2, _ = engulfing(o2, h2, l2, c2)
    assert bullish2[1] is False


def test_bearish_engulfing_positive():
    o, h, l, c = ohlc([
        (100, 111, 99, 110),
        (112, 113, 98, 99),
    ])
    _, bearish = engulfing(o, h, l, c)
    assert bearish[1] is True


def test_bullish_harami_positive_and_negative():
    o, h, l, c = ohlc([
        (110, 112, 94, 95),
        (100, 106, 99, 105),
    ])
    bullish, _ = harami(o, h, l, c)
    assert bullish[1] is True

    o2, h2, l2, c2 = ohlc([
        (110, 112, 94, 95),
        (94, 106, 90, 105),
    ])
    bullish2, _ = harami(o2, h2, l2, c2)
    assert bullish2[1] is False


def test_bearish_harami_positive():
    o, h, l, c = ohlc([
        (95, 112, 94, 110),
        (105, 106, 99, 100),
    ])
    _, bearish = harami(o, h, l, c)
    assert bearish[1] is True


def test_piercing_line_positive_and_negative():
    o, h, l, c = ohlc([
        (110, 112, 98, 100),
        (95, 109, 94, 108),
    ])
    piercing, _ = piercing_dark_cloud_cover(o, h, l, c)
    assert piercing[1] is True

    o2, h2, l2, c2 = ohlc([
        (110, 112, 98, 100),
        (95, 103, 94, 102),
    ])
    piercing2, _ = piercing_dark_cloud_cover(o2, h2, l2, c2)
    assert piercing2[1] is False


def test_dark_cloud_cover_positive():
    o, h, l, c = ohlc([
        (100, 112, 98, 110),
        (115, 116, 102, 103),
    ])
    _, dark_cloud = piercing_dark_cloud_cover(o, h, l, c)
    assert dark_cloud[1] is True


def test_tweezer_top_positive_and_negative():
    o, h, l, c = ohlc([
        (100, 110, 99, 108),
        (109, 110, 100, 101),
        (109, 111, 100, 101),
    ])
    top, _ = tweezer(o, h, l, c)
    assert top[1] is True

    o2, h2, l2, c2 = ohlc([
        (100, 110, 99, 108),
        (109, 111, 100, 101),
    ])
    top2, _ = tweezer(o2, h2, l2, c2)
    assert top2[1] is False


def test_tweezer_bottom_positive():
    o, h, l, c = ohlc([
        (108, 109, 98, 100),
        (99, 108, 98, 107),
    ])
    _, bottom = tweezer(o, h, l, c)
    assert bottom[1] is True


def test_morning_star_positive_and_negative():
    o, h, l, c = ohlc([
        (110, 111, 99, 100),
        (97, 99, 96, 98),
        (97, 109, 96, 108),
    ])
    morning, _ = morning_evening_star(o, h, l, c)
    assert morning[2] is True

    o2, h2, l2, c2 = ohlc([
        (110, 111, 99, 100),
        (97, 99, 96, 98),
        (97, 104, 96, 103),
    ])
    morning2, _ = morning_evening_star(o2, h2, l2, c2)
    assert morning2[2] is False


def test_evening_star_positive():
    o, h, l, c = ohlc([
        (100, 111, 99, 110),
        (113, 115, 112, 114),
        (113, 114, 101, 102),
    ])
    _, evening = morning_evening_star(o, h, l, c)
    assert evening[2] is True


def test_three_white_soldiers_positive_and_negative():
    o, h, l, c = ohlc([
        (100, 106.5, 99, 106),
        (103, 110.3, 102.7, 110),
        (107, 114.2, 106.8, 114),
    ])
    soldiers, _ = three_soldiers_crows(o, h, l, c)
    assert soldiers[2] is True

    o2, h2, l2, c2 = ohlc([
        (100, 106.5, 99, 106),
        (103, 110.3, 102.7, 110),
        (111, 115.2, 110.8, 115),
    ])
    soldiers2, _ = three_soldiers_crows(o2, h2, l2, c2)
    assert soldiers2[2] is False


def test_three_black_crows_positive_and_negative():
    o, h, l, c = ohlc([
        (106, 106.2, 99.5, 100),
        (103, 103.3, 96.5, 97),
        (99, 99.3, 92.5, 93),
    ])
    _, crows = three_soldiers_crows(o, h, l, c)
    assert crows[2] is True

    o2, h2, l2, c2 = ohlc([
        (106, 106.2, 99.5, 100),
        (103, 103.3, 96.5, 97),
        (95, 95.3, 88.5, 89),
    ])
    _, crows2 = three_soldiers_crows(o2, h2, l2, c2)
    assert crows2[2] is False
