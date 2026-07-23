from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from src.pipeline.indicators import ema, macd, rsi, sma

TEN_DP = Decimal("0.0000000001")


def q(value):
    return value.quantize(TEN_DP, rounding=ROUND_HALF_UP)


def to_decimals(values):
    return [Decimal(v) for v in values]


def test_sma_period_5():
    prices = to_decimals([10, 12, 14, 11, 13, 15, 17, 14, 16, 18])
    result = sma(prices, 5)

    assert result[0] is None
    assert result[1] is None
    assert result[2] is None
    assert result[3] is None
    assert result[4] == Decimal(12)
    assert result[6] == Decimal(14)
    assert result[9] == Decimal(16)


def test_ema_period_5_seed_and_recursion():
    prices = to_decimals([10, 12, 14, 11, 13, 15, 16, 17, 18, 19])
    result = ema(prices, 5)

    assert result[3] is None
    assert result[4] == Decimal(12)
    assert result[5] == Decimal(13)
    assert result[6] == Decimal(14)
    assert result[7] == Decimal(15)
    assert result[8] == Decimal(16)
    assert result[9] == Decimal(17)


def test_rsi_period_14_wilder_worked_example():
    prices = to_decimals([100, 110, 106, 116, 112, 122, 118, 128, 124, 134, 130, 140, 136, 146, 142, 152])
    result = rsi(prices, 14)

    assert result[13] is None
    assert q(result[14]) == Decimal("71.4285714286")
    assert q(result[15]) == Decimal("74.2574257426")


def test_rsi_period_14_naive_average_would_diverge_from_wilder():
    prices = to_decimals([100, 110, 106, 116, 112, 122, 118, 128, 124, 134, 130, 140, 136, 146, 142, 152])
    result = rsi(prices, 14)

    naive_avg_gain_15 = (Decimal(70) - Decimal(10) + Decimal(10)) / Decimal(14)
    wilder_avg_gain_15 = (Decimal(5) * 13 + Decimal(10)) / Decimal(14)

    assert naive_avg_gain_15 == Decimal(5)
    assert wilder_avg_gain_15 != naive_avg_gain_15
    assert q(result[15]) != Decimal("74.0000000000")


def test_rsi_period_14_zero_average_loss_returns_100():
    prices = to_decimals(list(range(100, 115)))
    result = rsi(prices, 14)

    assert result[14] == Decimal(100)


def test_macd_12_26_9():
    raw_prices = []
    price = 100
    raw_prices.append(price)
    for i in range(1, 34):
        price = price + (1 if i <= 16 else 3)
        raw_prices.append(price)

    prices = to_decimals(raw_prices)
    macd_line, signal_line, histogram = macd(prices, 12, 26, 9)

    assert macd_line[24] is None
    assert q(macd_line[25]) == Decimal("12.9843501833")
    assert q(macd_line[26]) == Decimal("13.3829883975")
    assert q(macd_line[33]) == Decimal("15.9906903879")

    assert all(signal_line[i] is None for i in range(25, 33))
    assert q(signal_line[33]) == Decimal("14.5314462035")

    assert all(histogram[i] is None for i in range(25, 33))
    assert q(histogram[33]) == Decimal("1.4592441844")
    assert q(macd_line[33] - signal_line[33]) == q(histogram[33])
