from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models import (
    BacktestResult,
    Company,
    DailyPrice,
    SignalCall,
    SignalConfidence,
    SignalTimeframe,
    SymbolLiquidityTier,
    TechnicalSignal,
)


def _number(value: Any) -> float | None:
    return float(value) if value is not None else None


def _rating(score: int) -> str:
    if score >= 85:
        return "strong_setup"
    if score >= 70:
        return "positive_setup"
    if score >= 50:
        return "neutral"
    return "weak"


def _liquidity_score(tier: Any) -> int:
    if not tier:
        return 0
    value = str(tier).lower()
    if "high" in value:
        return 15
    if "medium" in value:
        return 10
    return 5


def _confidence_score(rows: list[Any], signal_names: set[str]) -> int:
    score = 0
    for row in rows:
        if row.signal_name in signal_names:
            tier = str(row.tier).lower()
            if "high_confidence" in tier:
                score = max(score, 20)
            elif "low_sample" in tier:
                score = max(score, 10)
    return score


def _backtest_summary(rows: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "signal_name": row.signal_name,
            "win_rate": _number(row.win_rate),
            "sample_size": row.sample_size,
            "average_return": _number(row.mean_return),
        }
        for row in rows
    ]


def build_stock_intelligence(session: Session, symbol: str) -> dict[str, Any] | None:
    company = session.execute(select(Company).where(Company.symbol == symbol)).scalar_one_or_none()
    if not company:
        return None

    technical = session.execute(
        select(TechnicalSignal)
        .where(TechnicalSignal.symbol == symbol, TechnicalSignal.timeframe == SignalTimeframe.DAILY)
        .order_by(TechnicalSignal.date.desc())
        .limit(1)
    ).scalar_one_or_none()

    price = session.execute(
        select(DailyPrice.close).where(DailyPrice.symbol == symbol).order_by(DailyPrice.date.desc()).limit(1)
    ).scalar_one_or_none()

    liquidity = session.execute(
        select(SymbolLiquidityTier.liquidity_tier).where(SymbolLiquidityTier.symbol == symbol)
    ).scalar_one_or_none()

    calls = session.execute(
        select(SignalCall).where(SignalCall.symbol == symbol).order_by(SignalCall.created_at.desc()).limit(10)
    ).scalars().all()

    signal_names = {str(call.signal_name) for call in calls if getattr(call, "signal_name", None)}

    confidence = session.execute(select(SignalConfidence)).scalars().all()
    backtests = session.execute(select(BacktestResult).order_by(BacktestResult.sample_size.desc()).limit(10)).scalars().all()

    trend = 0
    momentum = 0
    risks: list[str] = []
    explanation: list[str] = []

    if technical:
        if technical.sma_50 and technical.sma_200 and technical.sma_50 > technical.sma_200:
            trend += 15
            explanation.append("SMA50 is above SMA200")
        if price and technical.sma_200 and Decimal(str(price)) > Decimal(str(technical.sma_200)):
            trend += 15
            explanation.append("Price is above SMA200")

        if technical.rsi_14:
            rsi = float(technical.rsi_14)
            if 40 <= rsi <= 65:
                momentum += 10
            elif rsi < 30:
                momentum += 15
                explanation.append("RSI shows recovery opportunity")
            elif rsi > 70:
                risks.append("RSI is overbought")

        if technical.macd_line and technical.macd_signal and technical.macd_line > technical.macd_signal:
            momentum += 10
            explanation.append("MACD bullish confirmation")

    liquidity_score = _liquidity_score(liquidity)
    reliability = _confidence_score(confidence, signal_names)
    risk_adjustment = max(-10, -(len(risks) * 5))

    score = max(0, min(100, trend + momentum + liquidity_score + reliability + risk_adjustment))

    return {
        "symbol": symbol,
        "company_name": company.company_name,
        "sector": company.sector,
        "artha_score": score,
        "rating": _rating(score),
        "scores": {
            "trend": trend,
            "momentum": momentum,
            "liquidity": liquidity_score,
            "reliability": reliability,
            "risk_adjustment": risk_adjustment,
        },
        "technical": {
            "rsi": _number(technical.rsi_14) if technical else None,
            "macd": "bullish" if technical and technical.macd_line and technical.macd_signal and technical.macd_line > technical.macd_signal else "neutral",
        },
        "confidence": [
            {"signal_name": row.signal_name, "tier": str(row.tier)}
            for row in confidence
            if row.signal_name in signal_names
        ],
        "backtest_summary": _backtest_summary(backtests),
        "signals": [{"status": str(call.status)} for call in calls],
        "risk": {"level": "medium" if risks else "low", "warnings": risks},
        "explanation": explanation,
    }
