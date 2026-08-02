from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from src.api.cache import cached, technical_signals_cache
from src.api.db_readonly import get_readonly_session
from src.api.rate_limit import PUBLIC_RATE_LIMIT, limiter
from src.database.models import ActionType, Company, CorporateAction, DailyPrice, Fundamental, SignalTimeframe, TechnicalSignal
from src.pipeline.fundamental_ratios import payout_ratio

router = APIRouter(prefix="/stocks", tags=["stocks"])

FACE_VALUE = Decimal("100")


def _row_to_dict(row: Any, exclude: set[str]) -> dict[str, Any]:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns if c.name not in exclude}


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
    }


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
    _load_company(symbol)

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

    return result
