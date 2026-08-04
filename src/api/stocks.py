from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from src.api.cache import cached, price_history_cache, technical_signals_cache
from src.api.db_readonly import get_readonly_session
from src.api.rate_limit import PUBLIC_RATE_LIMIT, limiter
from src.database.models import (
    ActionType,
    Company,
    CorporateAction,
    DailyPrice,
    Fundamental,
    SectorFundamentalBaseline,
    SignalConfidence,
    SignalTimeframe,
    SymbolLiquidityTier,
    TechnicalSignal,
)
from src.pipeline.extract_signal_calls import DOJI_REQUIRED_LIQUIDITY_TIER, DOJI_SIGNAL_NAME, TARGET_SIGNAL_HORIZONS
from src.pipeline.fundamental_ratios import payout_ratio, sector_relative_valuation
from src.pipeline.run_signal_backtests import build_signal_conditions

router = APIRouter(prefix="/stocks", tags=["stocks"])

FACE_VALUE = Decimal("100")


def _row_to_dict(row: Any, exclude: set[str]) -> dict[str, Any]:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns if c.name not in exclude}


def evaluate_target_signal_conditions(
    signal_row: TechnicalSignal,
    close_price: Any,
    liquidity_tier: str | None,
    confidence_by_name: dict[str, SignalConfidence],
) -> list[dict[str, Any]]:
    conditions = build_signal_conditions()

    results = []
    for signal_name in TARGET_SIGNAL_HORIZONS:
        condition_fn = conditions[signal_name]
        active = bool(condition_fn(signal_row, close_price, None, None))

        if signal_name == DOJI_SIGNAL_NAME and liquidity_tier != DOJI_REQUIRED_LIQUIDITY_TIER:
            active = False

        confidence = confidence_by_name.get(signal_name)
        results.append(
            {
                "signal_name": signal_name,
                "active": active,
                "tier": confidence.tier.value if confidence is not None else None,
                "avg_win_rate_minus_baseline": confidence.avg_win_rate_minus_baseline if confidence is not None else None,
                "recommended_holding_period": confidence.recommended_holding_period if confidence is not None else None,
                "cost_viability_note": confidence.cost_viability_note if confidence is not None else None,
            }
        )
    return results


def _load_company(symbol: str) -> Company:
    with get_readonly_session() as session:
        company = session.execute(select(Company).where(Company.symbol == symbol)).scalar_one_or_none()
        if company is not None:
            session.expunge(company)

    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown symbol: {symbol}")
    return company


@router.get("/{symbol}/summary")
@limiter.limit(PUBLIC_RATE_LIMIT)
def get_stock_summary(symbol: str, request: Request) -> dict[str, Any]:
    company = _load_company(symbol)

    with get_readonly_session() as session:
        rows = session.execute(
            select(DailyPrice)
            .where(DailyPrice.symbol == symbol)
            .order_by(DailyPrice.date.desc())
            .limit(2)
        ).scalars().all()
        session.expunge_all()

    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No price data for symbol: {symbol}")

    latest = rows[0]
    previous = rows[1] if len(rows) > 1 else None

    percent_change = None
    if previous is not None and previous.close:
        percent_change = (latest.close - previous.close) / previous.close * 100

    return {
        "symbol": company.symbol,
        "company_name": company.company_name,
        "sector": company.sector,
        "instrument_type": company.instrument_type,
        "status": company.status,
        "latest_date": latest.date,
        "latest_close": latest.close,
        "previous_close": previous.close if previous else None,
        "percent_change": percent_change,
        "volume": latest.volume,
        "day_high": latest.high,
        "day_low": latest.low,
    }


@router.get("/{symbol}/history")
@limiter.limit(PUBLIC_RATE_LIMIT)
@cached(price_history_cache, key_fn=lambda **kwargs: (kwargs.get("symbol"), kwargs.get("days")))
def get_stock_history(symbol: str, request: Request, days: int = 180) -> list[dict[str, Any]]:
    _load_company(symbol)

    days = max(1, min(days, 730))

    with get_readonly_session() as session:
        rows = session.execute(
            select(DailyPrice)
            .where(DailyPrice.symbol == symbol)
            .order_by(DailyPrice.date.desc())
            .limit(days)
        ).scalars().all()
        session.expunge_all()

    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No price data for symbol: {symbol}")

    return [
        {
            "date": row.date,
            "open": row.open,
            "high": row.high,
            "low": row.low,
            "close": row.close,
            "volume": row.volume,
        }
        for row in reversed(rows)
    ]


@router.get("/{symbol}/technical")
@limiter.limit(PUBLIC_RATE_LIMIT)
@cached(technical_signals_cache, key_fn=lambda **kwargs: kwargs.get("symbol"))
def get_stock_technical(symbol: str, request: Request) -> dict[str, Any]:
    _load_company(symbol)

    with get_readonly_session() as session:
        row = session.execute(
            select(TechnicalSignal)
            .where(TechnicalSignal.symbol == symbol)
            .where(TechnicalSignal.timeframe == SignalTimeframe.DAILY)
            .order_by(TechnicalSignal.date.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is not None:
            session.expunge(row)

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No technical signals for symbol: {symbol}")

    return _row_to_dict(row, exclude={"id"})


@router.get("/{symbol}/fundamental")
@limiter.limit(PUBLIC_RATE_LIMIT)
def get_stock_fundamental(symbol: str, request: Request) -> dict[str, Any]:
    company = _load_company(symbol)

    with get_readonly_session() as session:
        fundamental = session.execute(
            select(Fundamental)
            .where(Fundamental.symbol == symbol)
            .order_by(Fundamental.reported_date.desc())
            .limit(1)
        ).scalar_one_or_none()
        if fundamental is not None:
            session.expunge(fundamental)

        dividend = session.execute(
            select(CorporateAction)
            .where(CorporateAction.symbol == symbol)
            .where(CorporateAction.action_type == ActionType.DIVIDEND)
            .order_by(CorporateAction.action_date.desc())
            .limit(1)
        ).scalar_one_or_none()
        if dividend is not None:
            session.expunge(dividend)

        sector_baseline = None
        if company.sector is not None:
            sector_baseline = session.execute(
                select(SectorFundamentalBaseline).where(SectorFundamentalBaseline.sector == company.sector)
            ).scalar_one_or_none()
            if sector_baseline is not None:
                session.expunge(sector_baseline)

    if fundamental is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No fundamentals for symbol: {symbol}")

    result = _row_to_dict(fundamental, exclude={"id"})

    result["dividend_percent"] = None
    result["dividend_fiscal_year"] = None
    result["payout_ratio"] = None

    if dividend is not None:
        result["dividend_percent"] = dividend.ratio_or_amount
        result["dividend_fiscal_year"] = dividend.fiscal_year

        if fundamental.eps is not None:
            dividend_per_share = Decimal(str(dividend.ratio_or_amount)) / Decimal("100") * FACE_VALUE
            eps = Decimal(str(fundamental.eps))
            ratio = payout_ratio(dividend_per_share, eps)
            result["payout_ratio"] = ratio

    result["sector_avg_pe"] = None
    result["sector_avg_pb"] = None
    result["sector_pe_relative_percent"] = None

    if sector_baseline is not None:
        result["sector_avg_pe"] = sector_baseline.avg_pe
        result["sector_avg_pb"] = sector_baseline.avg_pb

        if fundamental.pe_ratio is not None and sector_baseline.avg_pe is not None:
            company_pe = Decimal(str(fundamental.pe_ratio))
            sector_avg_pe = Decimal(str(sector_baseline.avg_pe))
            result["sector_pe_relative_percent"] = sector_relative_valuation(company_pe, sector_avg_pe)

    return result


@router.get("/{symbol}/signals")
@limiter.limit(PUBLIC_RATE_LIMIT)
def get_stock_signals(symbol: str, request: Request) -> dict[str, Any]:
    _load_company(symbol)

    with get_readonly_session() as session:
        joined = session.execute(
            select(TechnicalSignal, DailyPrice.close)
            .join(DailyPrice, (DailyPrice.symbol == TechnicalSignal.symbol) & (DailyPrice.date == TechnicalSignal.date))
            .where(TechnicalSignal.symbol == symbol)
            .where(TechnicalSignal.timeframe == SignalTimeframe.DAILY)
            .order_by(TechnicalSignal.date.desc())
            .limit(1)
        ).first()

        signal_row = None
        close_price = None
        if joined is not None:
            signal_row, close_price = joined
            session.expunge(signal_row)

        liquidity_tier = session.execute(
            select(SymbolLiquidityTier.liquidity_tier).where(SymbolLiquidityTier.symbol == symbol)
        ).scalar_one_or_none()

        confidence_rows = session.execute(
            select(SignalConfidence).where(SignalConfidence.signal_name.in_(TARGET_SIGNAL_HORIZONS))
        ).scalars().all()
        session.expunge_all()

    if signal_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No technical signals for symbol: {symbol}")

    confidence_by_name = {c.signal_name: c for c in confidence_rows}
    results = evaluate_target_signal_conditions(signal_row, close_price, liquidity_tier, confidence_by_name)

    return {"symbol": symbol, "as_of_date": signal_row.date, "signals": results}
