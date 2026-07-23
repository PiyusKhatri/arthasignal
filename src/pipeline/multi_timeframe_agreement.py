from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from src.database.connection import get_session
from src.database.models import DailyPrice, SignalTimeframe, TechnicalSignal

TIMEFRAMES = (SignalTimeframe.DAILY, SignalTimeframe.WEEKLY, SignalTimeframe.MONTHLY)


def _classify_rsi(rsi_14: Decimal | None) -> str:
    if rsi_14 is None:
        return "neutral"
    if rsi_14 < 30:
        return "bullish"
    if rsi_14 > 70:
        return "bearish"
    return "neutral"


def _classify_macd(macd_line: Decimal | None, macd_signal: Decimal | None) -> str:
    if macd_line is None or macd_signal is None:
        return "neutral"
    if macd_line > macd_signal:
        return "bullish"
    if macd_line < macd_signal:
        return "bearish"
    return "neutral"


def _classify_trend(sma_20: Decimal | None, sma_50: Decimal | None) -> str:
    if sma_20 is None or sma_50 is None:
        return "neutral"
    if sma_20 > sma_50:
        return "bullish"
    if sma_20 < sma_50:
        return "bearish"
    return "neutral"


def _overall_state(states: list[str]) -> str:
    if states.count("bullish") >= 2:
        return "bullish"
    if states.count("bearish") >= 2:
        return "bearish"
    return "neutral"


def _default_as_of_date(symbol: str) -> date:
    with get_session() as session:
        return session.execute(
            select(func.max(DailyPrice.date)).where(DailyPrice.symbol == symbol)
        ).scalar_one()


def _latest_signal_row(symbol: str, timeframe: SignalTimeframe, as_of_date: date) -> dict[str, Any] | None:
    with get_session() as session:
        row = session.execute(
            select(TechnicalSignal)
            .where(
                TechnicalSignal.symbol == symbol,
                TechnicalSignal.timeframe == timeframe,
                TechnicalSignal.date <= as_of_date,
            )
            .order_by(TechnicalSignal.date.desc())
            .limit(1)
        ).scalar_one_or_none()

        if row is None:
            return None

        return {
            "date": row.date,
            "rsi_14": row.rsi_14,
            "macd_line": row.macd_line,
            "macd_signal": row.macd_signal,
            "sma_20": row.sma_20,
            "sma_50": row.sma_50,
        }


def compute_multi_timeframe_agreement(symbol: str, as_of_date: date | None = None) -> dict[str, Any]:
    if as_of_date is None:
        as_of_date = _default_as_of_date(symbol)

    timeframe_states: dict[str, Any] = {}
    for timeframe in TIMEFRAMES:
        raw = _latest_signal_row(symbol, timeframe, as_of_date)
        if raw is None:
            timeframe_states[timeframe.value] = None
            continue

        rsi_state = _classify_rsi(raw["rsi_14"])
        macd_state = _classify_macd(raw["macd_line"], raw["macd_signal"])
        trend_state = _classify_trend(raw["sma_20"], raw["sma_50"])

        timeframe_states[timeframe.value] = {
            "as_of": raw["date"],
            "rsi_14": raw["rsi_14"],
            "rsi_state": rsi_state,
            "macd_line": raw["macd_line"],
            "macd_signal": raw["macd_signal"],
            "macd_state": macd_state,
            "sma_20": raw["sma_20"],
            "sma_50": raw["sma_50"],
            "trend_state": trend_state,
            "overall_state": _overall_state([rsi_state, macd_state, trend_state]),
        }

    overall_states = [v["overall_state"] for v in timeframe_states.values() if v is not None]
    counts = {s: overall_states.count(s) for s in ("bullish", "bearish", "neutral")}
    agreement_score = max(counts.values()) if counts else 0
    tied_states = [s for s, c in counts.items() if c == agreement_score and c > 0]
    majority_state = tied_states[0] if len(tied_states) == 1 else "mixed"

    return {
        "symbol": symbol,
        "as_of_date": as_of_date,
        "timeframes": timeframe_states,
        "timeframes_available": len(overall_states),
        "agreement_score": agreement_score,
        "majority_state": majority_state,
    }
