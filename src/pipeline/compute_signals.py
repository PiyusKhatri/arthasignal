from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import DailyPrice, SignalTimeframe, TechnicalSignal
from src.pipeline.indicators import (
    atr,
    bollinger_bands,
    cci,
    doji,
    engulfing,
    ema,
    fibonacci_retracement,
    fifty_two_week_high_low,
    hammer_shape,
    harami,
    macd,
    marubozu,
    morning_evening_star,
    obv,
    piercing_dark_cloud_cover,
    pivot_points,
    roc,
    rsi,
    shooting_star_shape,
    sma,
    spinning_top,
    stochastic,
    three_soldiers_crows,
    tweezer,
    vwap,
)
from src.pipeline.multitimeframe import resample_ohlcv

FIFTY_TWO_WEEK_PERIODS = {
    SignalTimeframe.DAILY: 252,
    SignalTimeframe.WEEKLY: 52,
    SignalTimeframe.MONTHLY: 12,
}

FIBONACCI_COLUMN_KEYS = {
    "0.0": "fib_0",
    "23.6": "fib_236",
    "38.2": "fib_382",
    "50.0": "fib_50",
    "61.8": "fib_618",
    "78.6": "fib_786",
    "100.0": "fib_100",
}


def _load_daily_bars(symbol: str) -> list[dict[str, Any]]:
    with get_session() as session:
        rows = session.execute(
            select(
                DailyPrice.date,
                DailyPrice.open,
                DailyPrice.high,
                DailyPrice.low,
                DailyPrice.close,
                DailyPrice.volume,
            )
            .where(DailyPrice.symbol == symbol)
            .order_by(DailyPrice.date)
        ).all()
    return [
        {"date": r.date, "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume}
        for r in rows
    ]


def _load_bars(symbol: str, timeframe: SignalTimeframe) -> list[dict[str, Any]]:
    daily_bars = _load_daily_bars(symbol)
    if timeframe == SignalTimeframe.DAILY:
        return daily_bars
    freq = "W" if timeframe == SignalTimeframe.WEEKLY else "M"
    periods = resample_ohlcv(daily_bars, freq)
    return [
        {
            "date": p["period_end"],
            "open": p["open"],
            "high": p["high"],
            "low": p["low"],
            "close": p["close"],
            "volume": p["volume"],
        }
        for p in periods
    ]


def _compute_series(bars: list[dict[str, Any]], timeframe: SignalTimeframe) -> dict[str, list]:
    opens = [b["open"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    volumes = [b["volume"] for b in bars]

    macd_line, macd_signal, macd_histogram = macd(closes)
    stochastic_k, stochastic_d = stochastic(highs, lows, closes)
    bollinger_middle, bollinger_upper, bollinger_lower = bollinger_bands(closes, 20)
    fifty_two_week_high, fifty_two_week_low = fifty_two_week_high_low(highs, lows, FIFTY_TWO_WEEK_PERIODS[timeframe])
    pivot, pivot_r1, pivot_r2, pivot_r3, pivot_s1, pivot_s2, pivot_s3 = pivot_points(highs, lows, closes)
    fib_levels = fibonacci_retracement(highs, lows, 20)
    marubozu_bullish, marubozu_bearish = marubozu(opens, highs, lows, closes)
    bullish_engulfing, bearish_engulfing = engulfing(opens, highs, lows, closes)
    bullish_harami, bearish_harami = harami(opens, highs, lows, closes)
    piercing_line, dark_cloud_cover = piercing_dark_cloud_cover(opens, highs, lows, closes)
    tweezer_top, tweezer_bottom = tweezer(opens, highs, lows, closes)
    morning_star, evening_star = morning_evening_star(opens, highs, lows, closes)
    three_white_soldiers, three_black_crows = three_soldiers_crows(opens, highs, lows, closes)

    series = {
        "sma_20": sma(closes, 20),
        "sma_50": sma(closes, 50),
        "sma_100": sma(closes, 100),
        "sma_200": sma(closes, 200),
        "ema_20": ema(closes, 20),
        "ema_50": ema(closes, 50),
        "rsi_14": rsi(closes, 14),
        "macd_line": macd_line,
        "macd_signal": macd_signal,
        "macd_histogram": macd_histogram,
        "stochastic_k": stochastic_k,
        "stochastic_d": stochastic_d,
        "cci_20": cci(highs, lows, closes, 20),
        "roc_12": roc(closes, 12),
        "bollinger_middle": bollinger_middle,
        "bollinger_upper": bollinger_upper,
        "bollinger_lower": bollinger_lower,
        "atr_14": atr(highs, lows, closes, 14),
        "obv": obv(closes, volumes),
        "vwap_20": vwap(highs, lows, closes, volumes, 20),
        "fifty_two_week_high": fifty_two_week_high,
        "fifty_two_week_low": fifty_two_week_low,
        "pivot": pivot,
        "pivot_r1": pivot_r1,
        "pivot_r2": pivot_r2,
        "pivot_r3": pivot_r3,
        "pivot_s1": pivot_s1,
        "pivot_s2": pivot_s2,
        "pivot_s3": pivot_s3,
        "doji": doji(opens, highs, lows, closes),
        "marubozu_bullish": marubozu_bullish,
        "marubozu_bearish": marubozu_bearish,
        "hammer": hammer_shape(opens, highs, lows, closes),
        "shooting_star": shooting_star_shape(opens, highs, lows, closes),
        "spinning_top": spinning_top(opens, highs, lows, closes),
        "bullish_engulfing": bullish_engulfing,
        "bearish_engulfing": bearish_engulfing,
        "bullish_harami": bullish_harami,
        "bearish_harami": bearish_harami,
        "piercing_line": piercing_line,
        "dark_cloud_cover": dark_cloud_cover,
        "tweezer_top": tweezer_top,
        "tweezer_bottom": tweezer_bottom,
        "morning_star": morning_star,
        "evening_star": evening_star,
        "three_white_soldiers": three_white_soldiers,
        "three_black_crows": three_black_crows,
    }
    for ratio_key, column_key in FIBONACCI_COLUMN_KEYS.items():
        series[column_key] = fib_levels[ratio_key]

    return series


def _build_rows(symbol: str, timeframe: SignalTimeframe, bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    series = _compute_series(bars, timeframe)
    rows = []
    for i, bar in enumerate(bars):
        row = {"symbol": symbol, "date": bar["date"], "timeframe": timeframe.value}
        for column, values in series.items():
            row[column] = values[i]
        rows.append(row)
    return rows


def _upsert_signals(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    set_columns = [c for c in rows[0] if c not in ("symbol", "date", "timeframe")]
    stmt = pg_insert(TechnicalSignal).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol", "date", "timeframe"],
        set_={c: getattr(stmt.excluded, c) for c in set_columns},
    )
    with get_session() as session:
        session.execute(stmt)
    return len(rows)


def compute_and_store_signals(symbol: str, timeframe: str = "daily") -> int:
    tf = SignalTimeframe(timeframe)
    bars = _load_bars(symbol, tf)
    rows = _build_rows(symbol, tf, bars)
    return _upsert_signals(rows)
