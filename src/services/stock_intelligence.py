from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models import (
    BacktestResult,
    Company,
    DailyPrice,
    Fundamental,
    SignalCall,
    SignalCallStatus,
    SignalConfidence,
    SignalTimeframe,
    SymbolLiquidityTier,
    TechnicalSignal,
)
from src.pipeline.signal_validation_policy import VALIDATION_PROTOCOL_START_DATE, VALIDATION_SIGNAL_SPECS


def _number(value: Any) -> float | None:
    return float(value) if value is not None else None


def _enum_value(value: Any) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value))


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
    if "high" in value or value in {"a", "tier_a"}:
        return 15
    if "medium" in value or value in {"b", "tier_b"}:
        return 10
    return 5


def _confidence_score(rows: Iterable[Any], signal_names: set[str]) -> int:
    score = 0
    for row in rows:
        if row.signal_name not in signal_names:
            continue
        tier = _enum_value(row.tier).lower()
        if "high_confidence" in tier:
            score = max(score, 20)
        elif "low_sample" in tier:
            score = max(score, 10)
    return score


def _confidence_level(score: int) -> str:
    if score >= 20:
        return "high"
    if score >= 10:
        return "medium"
    return "low"


def _trend_strength(score: int) -> str:
    if score >= 25:
        return "strong"
    if score >= 15:
        return "moderate"
    return "weak"


def _signal_quality(reliability_score: int, signal_names: set[str]) -> str:
    if reliability_score >= 20 and signal_names:
        return "high"
    if reliability_score >= 10 or signal_names:
        return "medium"
    return "low"


def _signal_direction(signal_name: str) -> str:
    if signal_name in {"rsi_14 < 30 (oversold)", "close < bollinger_lower"}:
        return "bullish"
    if signal_name == "rsi_14 > 70 (overbought)":
        return "bearish"
    return "neutral"


def _backtest_summary(rows: Iterable[Any]) -> list[dict[str, Any]]:
    return [
        {
            "signal_name": row.signal_name,
            "forward_days": row.forward_days,
            "win_rate": _number(row.win_rate),
            "sample_size": row.sample_size,
            "average_return": _number(row.mean_return),
        }
        for row in rows
    ]


def _confidence_summary(rows: Iterable[Any], signal_names: set[str]) -> list[dict[str, Any]]:
    relevant = [row for row in rows if row.signal_name in signal_names]
    relevant.sort(
        key=lambda row: (
            0 if "high_confidence" in _enum_value(row.tier).lower() else 1,
            -(row.min_sample_size or 0),
            row.signal_name,
        )
    )
    return [
        {
            "signal_name": row.signal_name,
            "tier": _enum_value(row.tier),
            "edge_vs_baseline": _number(row.avg_win_rate_minus_baseline),
            "min_sample_size": row.min_sample_size,
            "recommended_holding_period": row.recommended_holding_period,
            "cost_viability_note": row.cost_viability_note,
        }
        for row in relevant
    ]


def _valuation_score(fundamental: Fundamental | None) -> tuple[int, list[str]]:
    """Valuation & fundamental health layer (0 to 10 points)."""
    if not fundamental:
        return 5, []  # Neutral baseline if fundamental is unavailable
    
    score = 0
    insights = []
    pe = _number(fundamental.pe_ratio)
    eps = _number(fundamental.eps)
    pb = _number(fundamental.pb_ratio)

    if eps is not None:
        if eps > 25:
            score += 4
            insights.append(f"Strong EPS (NPR {eps:.2f}) supports fundamental quality.")
        elif eps > 10:
            score += 3
        elif eps > 0:
            score += 2
        else:
            insights.append(f"Negative EPS (NPR {eps:.2f}) indicates operational headwind.")

    if pe is not None:
        if 0 < pe <= 18:
            score += 4
            insights.append(f"Attractive P/E valuation ({pe:.1f}x).")
        elif 18 < pe <= 30:
            score += 3
        elif pe > 45:
            score += 1
            insights.append(f"Elevated P/E ratio ({pe:.1f}x) reflects growth premium.")
        else:
            score += 2
    else:
        score += 2

    if pb is not None and 0 < pb <= 3.5:
        score += 2
    else:
        score += 1

    return min(10, max(0, score)), insights


def _build_payload(
    *,
    symbol: str,
    company_name: str,
    sector: str | None,
    technical: TechnicalSignal | None,
    fundamental: Fundamental | None,
    price: Any,
    liquidity: Any,
    calls: Iterable[SignalCall],
    confidence_rows: Iterable[SignalConfidence],
    backtests: Iterable[BacktestResult],
) -> dict[str, Any]:
    calls_list = list(calls)
    signal_names = {str(call.signal_name) for call in calls_list if getattr(call, "signal_name", None)}
    confidence_list = list(confidence_rows)

    trend_score = 0
    momentum_score = 0
    score_risks: list[str] = []
    warnings: list[str] = []
    strengths: list[str] = []
    explanation: list[str] = []

    price_number = _number(price)
    sma_50 = _number(technical.sma_50) if technical else None
    sma_200 = _number(technical.sma_200) if technical else None
    rsi = _number(technical.rsi_14) if technical else None

    price_vs_sma_200 = "unavailable"
    sma_50_vs_sma_200 = "unavailable"
    macd_state = "neutral"
    rsi_state = "unavailable"

    # 1. Trend Strength (Max 30 points)
    if technical:
        if technical.sma_50 is not None and technical.sma_200 is not None:
            if technical.sma_50 > technical.sma_200:
                trend_score += 15
                sma_50_vs_sma_200 = "above"
                strengths.append("Golden alignment: 50-day SMA is above 200-day SMA.")
                explanation.append("Long-term moving-average structure is bullish.")
            else:
                sma_50_vs_sma_200 = "below"
                warnings.append("50-day SMA is below 200-day SMA (bearish alignment).")

        if price is not None and technical.sma_200 is not None:
            if Decimal(str(price)) > Decimal(str(technical.sma_200)):
                trend_score += 15
                price_vs_sma_200 = "above"
                strengths.append("Price is trading comfortably above its 200-day SMA.")
                explanation.append("Price is holding above its long-term baseline reference.")
            else:
                price_vs_sma_200 = "below"
                warnings.append("Price is trading below its 200-day SMA reference line.")

    # 2. Momentum (Max 20 points)
    if technical:
        if technical.rsi_14 is not None:
            rsi_value = float(technical.rsi_14)
            if 42 <= rsi_value <= 68:
                momentum_score += 10
                rsi_state = "healthy"
                strengths.append("RSI (14) is in a balanced bullish momentum channel.")
            elif rsi_value < 30:
                momentum_score += 10
                rsi_state = "oversold"
                strengths.append("RSI is deeply oversold, setting up a mean-reversion opportunity.")
                explanation.append("RSI shows oversold bounce potential.")
            elif rsi_value > 70:
                rsi_state = "overbought"
                score_risks.append("RSI is overbought (>70)")
                warnings.append("RSI is in overbought territory; upside may experience near-term pause.")
            else:
                rsi_state = "neutral"
                momentum_score += 5

        if technical.macd_line is not None and technical.macd_signal is not None:
            if technical.macd_line > technical.macd_signal:
                momentum_score += 10
                macd_state = "bullish"
                strengths.append("MACD is positive and trending above its signal line.")
                explanation.append("MACD provides constructive momentum confirmation.")
            elif technical.macd_line < technical.macd_signal:
                macd_state = "bearish"
                warnings.append("MACD is below its signal line, reflecting weak momentum.")

    # 3. Liquidity (Max 15 points)
    liquidity_pts = _liquidity_score(liquidity)
    liquidity_label = str(liquidity) if liquidity is not None else None
    if liquidity_pts >= 15:
        strengths.append("High liquidity tier (Tier A) ensures clean trade execution.")
    elif 0 < liquidity_pts <= 5:
        warnings.append("Lower liquidity profile may present higher slippage during entries/exits.")

    # 4. Signal Reliability & Edge (Max 15 points)
    raw_conf = _confidence_score(confidence_list, signal_names)
    reliability_pts = min(15, int(raw_conf * 0.75)) if raw_conf > 0 else (5 if signal_names else 0)
    confidence_level = _confidence_level(raw_conf)
    if reliability_pts >= 12:
        strengths.append("Active signal has strong statistical edge validated over 15+ years of data.")
    elif signal_names and reliability_pts == 0:
        warnings.append("Current active signal does not yet have high historical confidence.")

    # 5. Risk Adjustment (Max 10 points)
    risk_deductions = len(score_risks) * 5
    if rsi_state == "overbought":
        risk_deductions += 3
    risk_pts = max(0, 10 - risk_deductions)
    if score_risks:
        warnings.extend(score_risks)

    # 6. Valuation & Fundamentals (Max 10 points)
    fundamental_pts, fund_insights = _valuation_score(fundamental)
    strengths.extend([f for f in fund_insights if "Strong" in f or "Attractive" in f])
    warnings.extend([f for f in fund_insights if "Negative" in f or "Elevated" in f])

    # Total Artha Score (0-100)
    score = max(0, min(100, trend_score + momentum_score + liquidity_pts + reliability_pts + risk_pts + fundamental_pts))

    if len(warnings) >= 3 or score < 45:
        risk_level = "high"
    elif len(warnings) >= 1 or score < 65:
        risk_level = "medium"
    else:
        risk_level = "low"

    if not explanation:
        explanation.append("Technical structure reflects neutral market consolidation.")

    backtest_data = _backtest_summary(backtests)
    signals_data = [
        {
            "signal_name": call.signal_name,
            "status": _enum_value(call.status),
            "entry_date": call.entry_date.isoformat() if call.entry_date else None,
            "forward_days_horizon": call.forward_days_horizon,
            "direction": _signal_direction(str(call.signal_name)),
        }
        for call in calls_list
    ]

    scores_breakdown = {
        "trend": trend_score,
        "momentum": momentum_score,
        "liquidity": liquidity_pts,
        "reliability": reliability_pts,
        "risk_adjustment": risk_pts,
        "valuation": fundamental_pts,
    }

    technical_summary = {
        "rsi": rsi,
        "rsi_state": rsi_state,
        "macd": macd_state,
        "latest_price": price_number,
        "sma_50": sma_50,
        "sma_200": sma_200,
        "price_vs_sma_200": price_vs_sma_200,
        "sma_50_vs_sma_200": sma_50_vs_sma_200,
    }

    from src.services.ai_analyst import generate_ai_analyst_commentary

    ai_commentary = generate_ai_analyst_commentary(
        symbol=symbol,
        company_name=company_name,
        sector=sector,
        as_of_date=technical.date.isoformat() if technical and technical.date else None,
        artha_score=score,
        rating=_rating(score),
        score_breakdown=scores_breakdown,
        strengths=strengths,
        warnings=warnings,
        technical=technical_summary,
        signals=signals_data,
        backtests=backtest_data,
    )

    return {
        "symbol": symbol,
        "company_name": company_name,
        "sector": sector,
        "as_of_date": technical.date.isoformat() if technical and technical.date else None,
        "artha_score": score,
        "rating": _rating(score),
        "confidence_level": confidence_level,
        "signal_quality": _signal_quality(raw_conf, signal_names),
        "trend_strength": _trend_strength(trend_score),
        "scores": scores_breakdown,
        "technical": technical_summary,
        "fundamental": {
            "pe_ratio": _number(fundamental.pe_ratio) if fundamental else None,
            "pb_ratio": _number(fundamental.pb_ratio) if fundamental else None,
            "eps": _number(fundamental.eps) if fundamental else None,
            "book_value": _number(fundamental.book_value) if fundamental else None,
            "score": fundamental_pts,
        },
        "liquidity": {"tier": liquidity_label, "score": liquidity_pts},
        "confidence": _confidence_summary(confidence_list, signal_names),
        "backtest_summary": backtest_data,
        "signals": signals_data,
        "risk": {"level": risk_level, "warnings": warnings},
        "strengths": strengths,
        "explanation": explanation,
        "ai_analysis": ai_commentary,
    }


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

    fundamental = session.execute(
        select(Fundamental)
        .where(Fundamental.symbol == symbol)
        .order_by(Fundamental.reported_date.desc())
        .limit(1)
    ).scalar_one_or_none()

    price = session.execute(
        select(DailyPrice.close).where(DailyPrice.symbol == symbol).order_by(DailyPrice.date.desc()).limit(1)
    ).scalar_one_or_none()

    liquidity = session.execute(
        select(SymbolLiquidityTier.liquidity_tier).where(SymbolLiquidityTier.symbol == symbol)
    ).scalar_one_or_none()

    validated_signal_names = tuple(VALIDATION_SIGNAL_SPECS)
    calls = (
        session.execute(
            select(SignalCall)
            .where(
                SignalCall.symbol == symbol,
                SignalCall.entry_date >= VALIDATION_PROTOCOL_START_DATE,
                SignalCall.signal_name.in_(validated_signal_names),
            )
            .order_by(SignalCall.entry_date.desc(), SignalCall.created_at.desc())
            .limit(10)
        )
        .scalars()
        .all()
    )
    signal_names = {str(call.signal_name) for call in calls if getattr(call, "signal_name", None)}

    if signal_names:
        confidence = (
            session.execute(select(SignalConfidence).where(SignalConfidence.signal_name.in_(signal_names)))
            .scalars()
            .all()
        )
        backtests = (
            session.execute(
                select(BacktestResult)
                .where(BacktestResult.signal_name.in_(signal_names))
                .order_by(BacktestResult.sample_size.desc())
                .limit(12)
            )
            .scalars()
            .all()
        )
    else:
        confidence = []
        backtests = []

    return _build_payload(
        symbol=symbol,
        company_name=company.company_name,
        sector=company.sector,
        technical=technical,
        fundamental=fundamental,
        price=price,
        liquidity=liquidity,
        calls=calls,
        confidence_rows=confidence,
        backtests=backtests,
    )


def build_market_intelligence(session: Session, limit: int = 200) -> dict[str, Any]:
    companies = (
        session.execute(
            select(Company)
            .where(Company.instrument_type == "Equity", Company.status == "A")
            .order_by(Company.symbol)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    if not companies:
        return {
            "as_of_date": None,
            "market": {"condition": "unavailable", "trend": "unavailable", "risk_level": "unavailable"},
            "top_opportunities": [],
            "high_confidence_signals": [],
            "sector_insights": [],
            "stocks": [],
        }

    symbols = [company.symbol for company in companies]

    technical_rows = (
        session.execute(
            select(TechnicalSignal)
            .where(TechnicalSignal.symbol.in_(symbols), TechnicalSignal.timeframe == SignalTimeframe.DAILY)
            .order_by(TechnicalSignal.symbol, TechnicalSignal.date.desc())
            .distinct(TechnicalSignal.symbol)
        )
        .scalars()
        .all()
    )
    technical_by_symbol = {row.symbol: row for row in technical_rows}

    price_rows = (
        session.execute(
            select(DailyPrice)
            .where(DailyPrice.symbol.in_(symbols))
            .order_by(DailyPrice.symbol, DailyPrice.date.desc())
            .distinct(DailyPrice.symbol)
        )
        .scalars()
        .all()
    )
    price_by_symbol = {row.symbol: row.close for row in price_rows}

    liquidity_rows = session.execute(
        select(SymbolLiquidityTier.symbol, SymbolLiquidityTier.liquidity_tier).where(
            SymbolLiquidityTier.symbol.in_(symbols)
        )
    ).all()
    liquidity_by_symbol = {row.symbol: row.liquidity_tier for row in liquidity_rows}

    validated_signal_names = tuple(VALIDATION_SIGNAL_SPECS)
    pending_calls = (
        session.execute(
            select(SignalCall)
            .where(
                SignalCall.symbol.in_(symbols),
                SignalCall.status == SignalCallStatus.PENDING,
                SignalCall.entry_date >= VALIDATION_PROTOCOL_START_DATE,
                SignalCall.signal_name.in_(validated_signal_names),
            )
            .order_by(SignalCall.entry_date.desc(), SignalCall.created_at.desc())
        )
        .scalars()
        .all()
    )
    calls_by_symbol: dict[str, list[SignalCall]] = defaultdict(list)
    for call in pending_calls:
        calls_by_symbol[call.symbol].append(call)

    confidence_rows = session.execute(select(SignalConfidence)).scalars().all()

    stocks: list[dict[str, Any]] = []
    latest_dates: list[str] = []
    for company in companies:
        technical = technical_by_symbol.get(company.symbol)
        calls = calls_by_symbol.get(company.symbol, [])[:10]
        payload = _build_payload(
            symbol=company.symbol,
            company_name=company.company_name,
            sector=company.sector,
            technical=technical,
            price=price_by_symbol.get(company.symbol),
            liquidity=liquidity_by_symbol.get(company.symbol),
            calls=calls,
            confidence_rows=confidence_rows,
            backtests=[],
        )
        if payload["as_of_date"]:
            latest_dates.append(payload["as_of_date"])
        stocks.append(
            {
                "symbol": payload["symbol"],
                "company_name": payload["company_name"],
                "sector": payload["sector"],
                "artha_score": payload["artha_score"],
                "rating": payload["rating"],
                "confidence_level": payload["confidence_level"],
                "liquidity_tier": payload["liquidity"]["tier"],
                "trend_strength": payload["trend_strength"],
                "signal_quality": payload["signal_quality"],
                "risk_level": payload["risk"]["level"],
                "trend_score": payload["scores"]["trend"],
                "momentum_score": payload["scores"]["momentum"],
                "reliability_score": payload["scores"]["reliability"],
                "active_signals": [signal["signal_name"] for signal in payload["signals"]],
                "strengths": payload["strengths"][:2],
            }
        )

    stocks.sort(key=lambda row: (row["artha_score"], row["reliability_score"], row["trend_score"]), reverse=True)

    analyzed = len(stocks)
    average_score = sum(row["artha_score"] for row in stocks) / analyzed if analyzed else 0.0
    average_trend = sum(row["trend_score"] for row in stocks) / analyzed if analyzed else 0.0
    positive_share = sum(1 for row in stocks if row["artha_score"] >= 70) / analyzed if analyzed else 0.0
    weak_share = sum(1 for row in stocks if row["artha_score"] < 50) / analyzed if analyzed else 0.0
    high_confidence_share = (
        sum(1 for row in stocks if row["confidence_level"] == "high") / analyzed if analyzed else 0.0
    )

    if average_score >= 65 and positive_share >= 0.30:
        condition = "bullish"
    elif average_score >= 55:
        condition = "constructive"
    elif average_score >= 45:
        condition = "mixed"
    else:
        condition = "defensive"

    if average_trend >= 20:
        market_trend = "bullish"
    elif average_trend >= 10:
        market_trend = "neutral"
    else:
        market_trend = "bearish"

    if weak_share >= 0.45:
        market_risk = "high"
    elif weak_share >= 0.25:
        market_risk = "medium"
    else:
        market_risk = "low"

    sector_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in stocks:
        sector_groups[row["sector"] or "Other"].append(row)

    sector_insights = []
    for sector, rows in sector_groups.items():
        rows.sort(key=lambda item: item["artha_score"], reverse=True)
        avg_score = sum(item["artha_score"] for item in rows) / len(rows)
        avg_trend = sum(item["trend_score"] for item in rows) / len(rows)
        sector_insights.append(
            {
                "sector": sector,
                "average_artha_score": round(avg_score, 1),
                "trend": "bullish" if avg_trend >= 20 else "neutral" if avg_trend >= 10 else "bearish",
                "high_confidence_count": sum(1 for item in rows if item["confidence_level"] == "high"),
                "stock_count": len(rows),
                "top_symbol": rows[0]["symbol"],
                "top_score": rows[0]["artha_score"],
            }
        )
    sector_insights.sort(key=lambda row: row["average_artha_score"], reverse=True)

    high_confidence = [row for row in stocks if row["confidence_level"] == "high" and row["active_signals"]]

    return {
        "as_of_date": max(latest_dates) if latest_dates else None,
        "market": {
            "condition": condition,
            "trend": market_trend,
            "risk_level": market_risk,
            "average_artha_score": round(average_score, 1),
            "positive_setup_share": round(positive_share, 4),
            "high_confidence_share": round(high_confidence_share, 4),
            "stocks_analyzed": analyzed,
        },
        "top_opportunities": stocks[:8],
        "high_confidence_signals": high_confidence[:8],
        "sector_insights": sector_insights,
        "stocks": stocks,
    }
