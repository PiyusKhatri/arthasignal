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


def _liquidity_score(value: Any) -> int:
    if value is None:
        return 0
    tier = str(value).lower()
    if "high" in tier or tier in {"a", "tier_a"}:
        return 15
    if "medium" in tier or tier in {"b", "tier_b"}:
        return 10
    return 5


def _risk_adjustment(risks: list[str]) -> int:
    return max(-10, -(len(risks) * 5))


def _rating(score: int) -> str:
    if score >= 85:
        return "strong_setup"
    if score >= 70:
        return "positive_setup"
    if score >= 50:
        return "neutral"
    return "weak"


def build_stock_intelligence(session: Session, symbol: str) -> dict[str, Any] | None:
    company = session.execute(select(Company).where(Company.symbol == symbol)).scalar_one_or_none()
    if company is None:
        return None

    technical = session.execute(
        select(TechnicalSignal)
        .where(TechnicalSignal.symbol == symbol)
        .where(TechnicalSignal.timeframe == SignalTimeframe.DAILY)
        .order_by(TechnicalSignal.date.desc())
        .limit(1)
    ).scalar_one_or_none()

    price = session.execute(select(DailyPrice.close).where(DailyPrice.symbol == symbol).order_by(DailyPrice.date.desc()).limit(1)).scalar_one_or_none()
    liquidity = session.execute(select(SymbolLiquidityTier.liquidity_tier).where(SymbolLiquidityTier.symbol == symbol)).scalar_one_or_none()
    confidence_rows = session.execute(select(SignalConfidence)).scalars().all()
    backtests = session.execute(select(BacktestResult).order_by(BacktestResult.sample_size.desc()).limit(5)).scalars().all()
    calls = session.execute(select(SignalCall).where(SignalCall.symbol == symbol).order_by(SignalCall.created_at.desc()).limit(5)).scalars().all()

    trend_score = 0
    momentum_score = 0
    reliability_score = 0
    explanations = []
    risks = []

    if technical:
        if technical.sma_50 and technical.sma_200 and technical.sma_50 > technical.sma_200:
            trend_score += 15
            explanations.append("SMA50 is above SMA200")
        if price and technical.sma_200 and Decimal(str(price)) > Decimal(str(technical.sma_200)):
            trend_score += 15
            explanations.append("Price is above SMA200")

        if technical.rsi_14:
            rsi = float(technical.rsi_14)
            if 40 <= rsi <= 70:
                momentum_score += 10
            elif rsi < 30:
                momentum_score += 15
                explanations.append("RSI indicates oversold recovery")
            elif rsi > 70:
                risks.append("RSI is overbought")

        if technical.macd_line and technical.macd_signal and technical.macd_line > technical.macd_signal:
            momentum_score += 10
            explanations.append("MACD bullish confirmation")

    for row in confidence_rows:
        tier = str(row.tier).lower()
        if "high_confidence" in tier:
            reliability_score += 20
            break

    liquidity_score = _liquidity_score(liquidity)
    risk_score = _risk_adjustment(risks)
    score = max(0, min(100, trend_score + momentum_score + liquidity_score + reliability_score + risk_score))

    return {
        "symbol": symbol,
        "company_name": company.company_name,
        "sector": company.sector,
        "artha_score": score,
        "rating": _rating(score),
        "scores": {
            "trend": trend_score,
            "momentum": momentum_score,
            "liquidity": liquidity_score,
            "reliability": reliability_score,
            "risk_adjustment": risk_score,
        },
        "technical": {
            "rsi": _number(technical.rsi_14) if technical else None,
            "macd": "bullish" if technical and technical.macd_line and technical.macd_signal and technical.macd_line > technical.macd_signal else "neutral",
        },
        "liquidity": {"tier": liquidity, "score": liquidity_score},
        "confidence": [{"signal_name": row.signal_name, "tier": str(row.tier)} for row in confidence_rows[:10]],
        "backtest_summary": [{"signal_name": row.signal_name, "win_rate": _number(row.win_rate), "sample_size": row.sample_size} for row in backtests],
        "signals": [{"status": str(call.status)} for call in calls],
        "risk": {"level": "medium" if risks else "low", "warnings": risks},
        "explanation": explanations,
    }
