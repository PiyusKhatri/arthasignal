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


def fifty_two_week_high_low(
    high: Sequence,
    low: Sequence,
    window: int = 252,
) -> tuple[list[Decimal | None], list[Decimal | None]]:
    highs = _to_decimal_list(high)
    lows = _to_decimal_list(low)
    length = len(highs)

    high_line: list[Decimal | None] = [None] * length
    low_line: list[Decimal | None] = [None] * length

    for i in range(window - 1, length):
        window_high = highs[i - window + 1 : i + 1]
        window_low = lows[i - window + 1 : i + 1]
        high_line[i] = max(window_high)
        low_line[i] = min(window_low)

    return high_line, low_line


def pivot_points(
    high: Sequence,
    low: Sequence,
    close: Sequence,
) -> tuple[
    list[Decimal | None],
    list[Decimal | None],
    list[Decimal | None],
    list[Decimal | None],
    list[Decimal | None],
    list[Decimal | None],
    list[Decimal | None],
]:
    highs = _to_decimal_list(high)
    lows = _to_decimal_list(low)
    closes = _to_decimal_list(close)
    length = len(closes)

    pivot: list[Decimal | None] = [None] * length
    r1: list[Decimal | None] = [None] * length
    r2: list[Decimal | None] = [None] * length
    r3: list[Decimal | None] = [None] * length
    s1: list[Decimal | None] = [None] * length
    s2: list[Decimal | None] = [None] * length
    s3: list[Decimal | None] = [None] * length

    for i in range(1, length):
        prev_h = highs[i - 1]
        prev_l = lows[i - 1]
        prev_c = closes[i - 1]

        p = (prev_h + prev_l + prev_c) / Decimal(3)
        pivot[i] = p
        r1[i] = Decimal(2) * p - prev_l
        s1[i] = Decimal(2) * p - prev_h
        r2[i] = p + (prev_h - prev_l)
        s2[i] = p - (prev_h - prev_l)
        r3[i] = prev_h + Decimal(2) * (p - prev_l)
        s3[i] = prev_l - Decimal(2) * (prev_h - p)

    return pivot, r1, r2, r3, s1, s2, s3


FIBONACCI_RATIOS = {
    "0.0": Decimal("0"),
    "23.6": Decimal("0.236"),
    "38.2": Decimal("0.382"),
    "50.0": Decimal("0.5"),
    "61.8": Decimal("0.618"),
    "78.6": Decimal("0.786"),
    "100.0": Decimal("1.0"),
}


def fibonacci_retracement(
    high: Sequence,
    low: Sequence,
    lookback: int = 20,
) -> dict[str, list[Decimal | None]]:
    highs = _to_decimal_list(high)
    lows = _to_decimal_list(low)
    length = len(highs)

    levels: dict[str, list[Decimal | None]] = {key: [None] * length for key in FIBONACCI_RATIOS}

    for i in range(lookback - 1, length):
        window_high = highs[i - lookback + 1 : i + 1]
        window_low = lows[i - lookback + 1 : i + 1]
        swing_high = max(window_high)
        swing_low = min(window_low)
        span = swing_high - swing_low

        for key, ratio in FIBONACCI_RATIOS.items():
            levels[key][i] = swing_high - ratio * span

    return levels


DOJI_BODY_RATIO = Decimal("0.05")
MARUBOZU_SHADOW_RATIO = Decimal("0.05")
SHADOW_MULTIPLE = Decimal("2")
MINIMAL_SHADOW_RATIO = Decimal("0.1")
SPINNING_TOP_MIN_BODY_RATIO = Decimal("0.05")
SPINNING_TOP_MAX_BODY_RATIO = Decimal("0.3")
SPINNING_TOP_SHADOW_SIMILARITY = Decimal("0.5")
HARAMI_MAX_BODY_RATIO = Decimal("0.5")
TWEEZER_TOLERANCE_RATIO = Decimal("0.001")
STAR_LARGE_BODY_RATIO = Decimal("0.6")
STAR_SMALL_BODY_RATIO = Decimal("0.3")
SOLDIERS_CROWS_LARGE_BODY_RATIO = Decimal("0.6")


def _candle_stats(open_: Sequence, high: Sequence, low: Sequence, close: Sequence):
    opens = _to_decimal_list(open_)
    highs = _to_decimal_list(high)
    lows = _to_decimal_list(low)
    closes = _to_decimal_list(close)
    length = len(closes)

    body_top = [max(opens[i], closes[i]) for i in range(length)]
    body_bottom = [min(opens[i], closes[i]) for i in range(length)]
    body = [body_top[i] - body_bottom[i] for i in range(length)]
    rng = [highs[i] - lows[i] for i in range(length)]
    upper = [highs[i] - body_top[i] for i in range(length)]
    lower = [body_bottom[i] - lows[i] for i in range(length)]
    bullish = [closes[i] > opens[i] for i in range(length)]

    return opens, highs, lows, closes, body_top, body_bottom, body, rng, upper, lower, bullish


def doji(open_: Sequence, high: Sequence, low: Sequence, close: Sequence) -> list[bool]:
    _, _, _, _, _, _, body, rng, _, _, _ = _candle_stats(open_, high, low, close)
    result = []
    for i in range(len(body)):
        if rng[i] == 0:
            result.append(True)
        else:
            result.append(body[i] <= DOJI_BODY_RATIO * rng[i])
    return result


def marubozu(open_: Sequence, high: Sequence, low: Sequence, close: Sequence) -> tuple[list[bool], list[bool]]:
    _, _, _, _, _, _, _, rng, upper, lower, bullish = _candle_stats(open_, high, low, close)
    length = len(rng)
    bullish_result = [False] * length
    bearish_result = [False] * length

    for i in range(length):
        if rng[i] == 0:
            continue
        shadows_small = upper[i] <= MARUBOZU_SHADOW_RATIO * rng[i] and lower[i] <= MARUBOZU_SHADOW_RATIO * rng[i]
        if not shadows_small:
            continue
        if bullish[i]:
            bullish_result[i] = True
        elif not bullish[i]:
            bearish_result[i] = True

    return bullish_result, bearish_result


def hammer_shape(open_: Sequence, high: Sequence, low: Sequence, close: Sequence) -> list[bool]:
    _, _, _, _, _, _, body, rng, upper, lower, _ = _candle_stats(open_, high, low, close)
    result = []
    for i in range(len(body)):
        if rng[i] == 0 or body[i] == 0:
            result.append(False)
            continue
        result.append(lower[i] >= SHADOW_MULTIPLE * body[i] and upper[i] <= MINIMAL_SHADOW_RATIO * rng[i])
    return result


def shooting_star_shape(open_: Sequence, high: Sequence, low: Sequence, close: Sequence) -> list[bool]:
    _, _, _, _, _, _, body, rng, upper, lower, _ = _candle_stats(open_, high, low, close)
    result = []
    for i in range(len(body)):
        if rng[i] == 0 or body[i] == 0:
            result.append(False)
            continue
        result.append(upper[i] >= SHADOW_MULTIPLE * body[i] and lower[i] <= MINIMAL_SHADOW_RATIO * rng[i])
    return result


def spinning_top(open_: Sequence, high: Sequence, low: Sequence, close: Sequence) -> list[bool]:
    _, _, _, _, _, _, body, rng, upper, lower, _ = _candle_stats(open_, high, low, close)
    result = []
    for i in range(len(body)):
        if rng[i] == 0:
            result.append(False)
            continue
        body_ratio_ok = SPINNING_TOP_MIN_BODY_RATIO * rng[i] < body[i] <= SPINNING_TOP_MAX_BODY_RATIO * rng[i]
        shadows_exceed_body = upper[i] > body[i] and lower[i] > body[i]
        if not (body_ratio_ok and shadows_exceed_body):
            result.append(False)
            continue
        larger = max(upper[i], lower[i])
        smaller = min(upper[i], lower[i])
        result.append(smaller >= SPINNING_TOP_SHADOW_SIMILARITY * larger)
    return result


def engulfing(open_: Sequence, high: Sequence, low: Sequence, close: Sequence) -> tuple[list[bool], list[bool]]:
    _, _, _, _, body_top, body_bottom, _, _, _, _, bullish = _candle_stats(open_, high, low, close)
    length = len(bullish)
    bullish_result = [False] * length
    bearish_result = [False] * length

    for i in range(1, length):
        contains_prior = body_top[i] > body_top[i - 1] and body_bottom[i] < body_bottom[i - 1]
        if not contains_prior:
            continue
        if not bullish[i - 1] and bullish[i]:
            bullish_result[i] = True
        elif bullish[i - 1] and not bullish[i]:
            bearish_result[i] = True

    return bullish_result, bearish_result


def harami(open_: Sequence, high: Sequence, low: Sequence, close: Sequence) -> tuple[list[bool], list[bool]]:
    _, _, _, _, body_top, body_bottom, body, _, _, _, bullish = _candle_stats(open_, high, low, close)
    length = len(bullish)
    bullish_result = [False] * length
    bearish_result = [False] * length

    for i in range(1, length):
        contained = body_top[i] < body_top[i - 1] and body_bottom[i] > body_bottom[i - 1]
        if not contained:
            continue
        if body[i - 1] == 0 or body[i] > HARAMI_MAX_BODY_RATIO * body[i - 1]:
            continue
        if not bullish[i - 1]:
            bullish_result[i] = True
        else:
            bearish_result[i] = True

    return bullish_result, bearish_result


def piercing_dark_cloud_cover(
    open_: Sequence,
    high: Sequence,
    low: Sequence,
    close: Sequence,
) -> tuple[list[bool], list[bool]]:
    opens, highs, lows, closes, _, _, _, _, _, _, bullish = _candle_stats(open_, high, low, close)
    length = len(bullish)
    piercing_result = [False] * length
    dark_cloud_result = [False] * length

    for i in range(1, length):
        prev_o = opens[i - 1]
        prev_h = highs[i - 1]
        prev_l = lows[i - 1]
        prev_c = closes[i - 1]
        midpoint = (prev_o + prev_c) / Decimal(2)

        if not bullish[i - 1] and bullish[i]:
            if opens[i] < prev_l and closes[i] > midpoint and closes[i] < prev_o:
                piercing_result[i] = True
        elif bullish[i - 1] and not bullish[i]:
            if opens[i] > prev_h and closes[i] < midpoint and closes[i] > prev_o:
                dark_cloud_result[i] = True

    return piercing_result, dark_cloud_result


def tweezer(open_: Sequence, high: Sequence, low: Sequence, close: Sequence) -> tuple[list[bool], list[bool]]:
    _, highs, lows, _, _, _, _, _, _, _, bullish = _candle_stats(open_, high, low, close)
    length = len(bullish)
    top_result = [False] * length
    bottom_result = [False] * length

    for i in range(1, length):
        if bullish[i - 1] and not bullish[i]:
            tolerance = TWEEZER_TOLERANCE_RATIO * max(highs[i - 1], highs[i])
            if abs(highs[i - 1] - highs[i]) <= tolerance:
                top_result[i] = True
        elif not bullish[i - 1] and bullish[i]:
            tolerance = TWEEZER_TOLERANCE_RATIO * max(lows[i - 1], lows[i])
            if abs(lows[i - 1] - lows[i]) <= tolerance:
                bottom_result[i] = True

    return top_result, bottom_result


def morning_evening_star(
    open_: Sequence,
    high: Sequence,
    low: Sequence,
    close: Sequence,
) -> tuple[list[bool], list[bool]]:
    opens, _, _, closes, body_top, body_bottom, body, rng, _, _, bullish = _candle_stats(open_, high, low, close)
    length = len(bullish)
    morning_result = [False] * length
    evening_result = [False] * length

    for i in range(2, length):
        i1, i2, i3 = i - 2, i - 1, i

        if rng[i1] == 0 or rng[i3] == 0:
            continue

        first_large = body[i1] >= STAR_LARGE_BODY_RATIO * rng[i1]
        third_large = body[i3] >= STAR_LARGE_BODY_RATIO * rng[i3]
        second_small = body[i2] <= STAR_SMALL_BODY_RATIO * rng[i1]
        midpoint = (opens[i1] + closes[i1]) / Decimal(2)

        if not first_large or not second_small or not third_large:
            continue

        if not bullish[i1] and bullish[i3]:
            if body_top[i2] < closes[i1] and closes[i3] > midpoint:
                morning_result[i] = True
        elif bullish[i1] and not bullish[i3]:
            if body_bottom[i2] > closes[i1] and closes[i3] < midpoint:
                evening_result[i] = True

    return morning_result, evening_result


def three_soldiers_crows(
    open_: Sequence,
    high: Sequence,
    low: Sequence,
    close: Sequence,
) -> tuple[list[bool], list[bool]]:
    opens, _, _, closes, body_top, body_bottom, body, rng, upper, lower, bullish = _candle_stats(
        open_, high, low, close
    )
    length = len(bullish)
    soldiers_result = [False] * length
    crows_result = [False] * length

    for i in range(2, length):
        i1, i2, i3 = i - 2, i - 1, i

        if any(rng[j] == 0 for j in (i1, i2, i3)):
            continue

        all_bullish = bullish[i1] and bullish[i2] and bullish[i3]
        all_bearish = not bullish[i1] and not bullish[i2] and not bullish[i3]

        if all_bullish:
            long_bodies = all(body[j] >= SOLDIERS_CROWS_LARGE_BODY_RATIO * rng[j] for j in (i1, i2, i3))
            opens_within = (
                body_bottom[i1] <= opens[i2] <= body_top[i1] and body_bottom[i2] <= opens[i3] <= body_top[i2]
            )
            closes_beyond = closes[i2] > closes[i1] and closes[i3] > closes[i2]
            minimal_shadow = all(upper[j] <= MINIMAL_SHADOW_RATIO * rng[j] for j in (i1, i2, i3))
            if long_bodies and opens_within and closes_beyond and minimal_shadow:
                soldiers_result[i] = True
        elif all_bearish:
            long_bodies = all(body[j] >= SOLDIERS_CROWS_LARGE_BODY_RATIO * rng[j] for j in (i1, i2, i3))
            opens_within = (
                body_bottom[i1] <= opens[i2] <= body_top[i1] and body_bottom[i2] <= opens[i3] <= body_top[i2]
            )
            closes_beyond = closes[i2] < closes[i1] and closes[i3] < closes[i2]
            minimal_shadow = all(lower[j] <= MINIMAL_SHADOW_RATIO * rng[j] for j in (i1, i2, i3))
            if long_bodies and opens_within and closes_beyond and minimal_shadow:
                crows_result[i] = True

    return soldiers_result, crows_result
