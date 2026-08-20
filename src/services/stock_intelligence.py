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

    price = session.execute(
        select(DailyPrice.close)
        .where(DailyPrice.symbol == symbol)
        .order_by(DailyPrice.date.desc())
        .limit(1)
    ).scalar_one_or_none()

    liquidity = session.execute(
        select(SymbolLiquidityTier.liquidity_tier)
        .where(SymbolLiquidityTier.symbol == symbol)
    ).scalar_one_or_none()

    confidence_rows = session.execute(select(SignalConfidence)).scalars().all()
    backtests = session.execute(select(BacktestResult).order_by(BacktestResult.sample_size.desc()).limit(5)).scalars().all()

    active_calls = session.execute(
        select(SignalCall)
        .where(SignalCall.symbol == symbol)
        .order_by(SignalCall.created_at.desc())
        .limit(5)
    ).scalars().all()

    trend_score = 0
    momentum_score = 0
    explanations: list[str] = []
    risks: list[str] = []

    if technical:
        if technical.sma_50 and technical.sma_200 and technical.sma_50 > technical.sma_200:
            trend_score += 20
            explanations.append("Medium-term trend is above long-term trend")
        if price and technical.sma_200 and Decimal(str(price)) > Decimal(str(technical.sma_200)):
            trend_score += 20
            explanations.append("Price is above SMA 200")
        if technical.rsi_14:
            rsi = float(technical.rsi_14)
            if 40 <= rsi <= 70:
                momentum_score += 15
                explanations.append("RSI is in a healthy momentum zone")
            elif rsi < 30:
                momentum_score += 20
                explanations.append("RSI indicates oversold conditions")
            elif rsi > 70:
                risks.append("RSI is approaching overbought territory")
        if technical.macd_line and technical.macd_signal and technical.macd_line > technical.macd_signal:
            momentum_score += 10
            explanations.append("MACD shows positive momentum")

    liquidity_score = _liquidity_score(liquidity)
    score = min(100, trend_score + momentum_score + liquidity_score)

    return {
        "symbol": symbol,
        "company_name": company.company_name,
        "sector": company.sector,
        "artha_score": score,
        "technical": {
            "rsi": _number(technical.rsi_14) if technical else None,
            "macd": "bullish" if technical and technical.macd_line and technical.macd_signal and technical.macd_line > technical.macd_signal else "neutral",
            "trend_score": trend_score,
            "momentum_score": momentum_score,
        },
        "liquidity": {"tier": liquidity, "score": liquidity_score},
        "confidence": [
            {"signal_name": row.signal_name, "tier": row.tier.value if hasattr(row.tier, "value") else row.tier}
            for row in confidence_rows[:10]
        ],
        "backtest_summary": [
            {"signal_name": row.signal_name, "win_rate": _number(row.win_rate), "sample_size": row.sample_size}
            for row in backtests
        ],
        "signals": [{"status": call.status.value if hasattr(call.status, "value") else call.status} for call in active_calls],
        "risk": {"level": "medium" if risks else "low", "warnings": risks},
        "explanation": explanations,
    }
