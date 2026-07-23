from __future__ import annotations

from decimal import Decimal
from typing import Sequence


def _to_decimal_list(prices) -> list[Decimal]:
    if hasattr(prices, "tolist"):
        prices = prices.tolist()
    return [Decimal(p) for p in prices]


def sma(prices: Sequence, period: int) -> list[Decimal | None]:
    values = _to_decimal_list(prices)
    result: list[Decimal | None] = [None] * len(values)

    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        result[i] = sum(window) / Decimal(period)

    return result


def ema(prices: Sequence, period: int) -> list[Decimal | None]:
    values = _to_decimal_list(prices)
    result: list[Decimal | None] = [None] * len(values)

    if len(values) < period:
        return result

    alpha = Decimal(2) / Decimal(period + 1)
    seed_index = period - 1
    seed = sum(values[0:period]) / Decimal(period)
    result[seed_index] = seed

    prev = seed
    for i in range(seed_index + 1, len(values)):
        current = alpha * values[i] + (Decimal(1) - alpha) * prev
        result[i] = current
        prev = current

    return result


def rsi(prices: Sequence, period: int) -> list[Decimal | None]:
    values = _to_decimal_list(prices)
    result: list[Decimal | None] = [None] * len(values)

    if len(values) < period + 1:
        return result

    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [c if c > 0 else Decimal(0) for c in changes]
    losses = [-c if c < 0 else Decimal(0) for c in changes]

    seed_index = period
    avg_gain = sum(gains[0:period]) / Decimal(period)
    avg_loss = sum(losses[0:period]) / Decimal(period)
    result[seed_index] = _rsi_from_averages(avg_gain, avg_loss)

    for i in range(seed_index + 1, len(values)):
        gain = gains[i - 1]
        loss = losses[i - 1]
        avg_gain = (avg_gain * (period - 1) + gain) / Decimal(period)
        avg_loss = (avg_loss * (period - 1) + loss) / Decimal(period)
        result[i] = _rsi_from_averages(avg_gain, avg_loss)

    return result


def _rsi_from_averages(avg_gain: Decimal, avg_loss: Decimal) -> Decimal:
    if avg_loss == 0:
        if avg_gain == 0:
            return Decimal(50)
        return Decimal(100)
    rs = avg_gain / avg_loss
    return Decimal(100) - Decimal(100) / (Decimal(1) + rs)


def macd(
    prices: Sequence,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> tuple[list[Decimal | None], list[Decimal | None], list[Decimal | None]]:
    values = _to_decimal_list(prices)
    length = len(values)

    ema_fast = ema(values, fast_period)
    ema_slow = ema(values, slow_period)

    macd_line: list[Decimal | None] = [None] * length
    for i in range(length):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd_line[i] = ema_fast[i] - ema_slow[i]

    signal_line: list[Decimal | None] = [None] * length
    histogram: list[Decimal | None] = [None] * length

    macd_indices = [i for i in range(length) if macd_line[i] is not None]
    if len(macd_indices) >= signal_period:
        alpha = Decimal(2) / Decimal(signal_period + 1)
        seed_positions = macd_indices[0:signal_period]
        seed_index = seed_positions[-1]
        seed = sum(macd_line[i] for i in seed_positions) / Decimal(signal_period)
        signal_line[seed_index] = seed
        histogram[seed_index] = macd_line[seed_index] - seed

        prev = seed
        for i in macd_indices[signal_period:]:
            current = alpha * macd_line[i] + (Decimal(1) - alpha) * prev
            signal_line[i] = current
            histogram[i] = macd_line[i] - current
            prev = current

    return macd_line, signal_line, histogram


def stochastic(
    high: Sequence,
    low: Sequence,
    close: Sequence,
    k_period: int = 14,
    d_period: int = 3,
) -> tuple[list[Decimal | None], list[Decimal | None]]:
    highs = _to_decimal_list(high)
    lows = _to_decimal_list(low)
    closes = _to_decimal_list(close)
    length = len(closes)

    k_line: list[Decimal | None] = [None] * length
    for i in range(k_period - 1, length):
        window_high = highs[i - k_period + 1 : i + 1]
        window_low = lows[i - k_period + 1 : i + 1]
        highest_high = max(window_high)
        lowest_low = min(window_low)
        if highest_high == lowest_low:
            k_line[i] = Decimal(0)
        else:
            k_line[i] = Decimal(100) * (closes[i] - lowest_low) / (highest_high - lowest_low)

    d_line: list[Decimal | None] = [None] * length
    k_indices = [i for i in range(length) if k_line[i] is not None]
    for position in range(d_period - 1, len(k_indices)):
        idx = k_indices[position]
        window_indices = k_indices[position - d_period + 1 : position + 1]
        d_line[idx] = sum(k_line[j] for j in window_indices) / Decimal(d_period)

    return k_line, d_line


def cci(high: Sequence, low: Sequence, close: Sequence, period: int = 20) -> list[Decimal | None]:
    highs = _to_decimal_list(high)
    lows = _to_decimal_list(low)
    closes = _to_decimal_list(close)
    length = len(closes)

    typical_prices = [(highs[i] + lows[i] + closes[i]) / Decimal(3) for i in range(length)]

    result: list[Decimal | None] = [None] * length
    for i in range(period - 1, length):
        window = typical_prices[i - period + 1 : i + 1]
        sma_tp = sum(window) / Decimal(period)
        mean_deviation = sum(abs(tp - sma_tp) for tp in window) / Decimal(period)
        if mean_deviation == 0:
            result[i] = None
        else:
            result[i] = (typical_prices[i] - sma_tp) / (Decimal("0.015") * mean_deviation)

    return result


def roc(prices: Sequence, period: int = 12) -> list[Decimal | None]:
    values = _to_decimal_list(prices)
    length = len(values)

    result: list[Decimal | None] = [None] * length
    for i in range(period, length):
        base = values[i - period]
        if base == 0:
            result[i] = None
        else:
            result[i] = Decimal(100) * (values[i] - base) / base

    return result


def bollinger_bands(
    prices: Sequence,
    period: int = 20,
    num_std: int = 2,
) -> tuple[list[Decimal | None], list[Decimal | None], list[Decimal | None]]:
    values = _to_decimal_list(prices)
    length = len(values)

    middle: list[Decimal | None] = [None] * length
    upper: list[Decimal | None] = [None] * length
    lower: list[Decimal | None] = [None] * length

    for i in range(period - 1, length):
        window = values[i - period + 1 : i + 1]
        mean = sum(window) / Decimal(period)
        variance = sum((v - mean) ** 2 for v in window) / Decimal(period)
        stddev = variance.sqrt()

        middle[i] = mean
        upper[i] = mean + Decimal(num_std) * stddev
        lower[i] = mean - Decimal(num_std) * stddev

    return middle, upper, lower


def atr(high: Sequence, low: Sequence, close: Sequence, period: int = 14) -> list[Decimal | None]:
    highs = _to_decimal_list(high)
    lows = _to_decimal_list(low)
    closes = _to_decimal_list(close)
    length = len(closes)

    result: list[Decimal | None] = [None] * length

    if length < period + 1:
        return result

    true_ranges: list[Decimal] = [Decimal(0)] * length
    for i in range(1, length):
        prev_close = closes[i - 1]
        true_ranges[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - prev_close),
            abs(lows[i] - prev_close),
        )

    seed_index = period
    seed = sum(true_ranges[1 : period + 1]) / Decimal(period)
    result[seed_index] = seed

    prev = seed
    for i in range(seed_index + 1, length):
        current = (prev * (period - 1) + true_ranges[i]) / Decimal(period)
        result[i] = current
        prev = current

    return result


def obv(close: Sequence, volume: Sequence) -> list[Decimal]:
    closes = _to_decimal_list(close)
    volumes = _to_decimal_list(volume)
    length = len(closes)

    result: list[Decimal] = [Decimal(0)] * length

    for i in range(1, length):
        if closes[i] > closes[i - 1]:
            result[i] = result[i - 1] + volumes[i]
        elif closes[i] < closes[i - 1]:
            result[i] = result[i - 1] - volumes[i]
        else:
            result[i] = result[i - 1]

    return result


def vwap(
    high: Sequence,
    low: Sequence,
    close: Sequence,
    volume: Sequence,
    period: int,
) -> list[Decimal | None]:
    highs = _to_decimal_list(high)
    lows = _to_decimal_list(low)
    closes = _to_decimal_list(close)
    volumes = _to_decimal_list(volume)
    length = len(closes)

    typical_prices = [(highs[i] + lows[i] + closes[i]) / Decimal(3) for i in range(length)]

    result: list[Decimal | None] = [None] * length
    for i in range(period - 1, length):
        window_tp = typical_prices[i - period + 1 : i + 1]
        window_vol = volumes[i - period + 1 : i + 1]
        numerator = sum(tp * vol for tp, vol in zip(window_tp, window_vol))
        denominator = sum(window_vol)
        if denominator == 0:
            result[i] = None
        else:
            result[i] = numerator / denominator

    return result
