from __future__ import annotations

from datetime import date, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select, text

from src.api.cache import cached, index_history_cache, intraday_today_cache, market_pulse_cache
from src.api.chart_ranges import VALID_RANGES, range_cutoff
from src.api.db_readonly import get_readonly_session
from src.api.rate_limit import PUBLIC_RATE_LIMIT, limiter
from src.api.stocks import evaluate_target_signal_conditions
from src.database.models import (
    Company,
    IntradayIndexSnapshot,
    MarketIndex,
    SignalConfidence,
    SignalTimeframe,
    SymbolLiquidityTier,
    TechnicalSignal,
)
from src.pipeline.extract_signal_calls import TARGET_SIGNAL_HORIZONS
from src.pipeline.market_pulse import compute_active_signals, compute_overall_market_pulse, compute_sector_wise_pulse

router = APIRouter(prefix="/market", tags=["market"])

NEPSE_INDEX_NAME = "NEPSE Index"


@router.get("/pulse")
@limiter.limit(PUBLIC_RATE_LIMIT)
@cached(market_pulse_cache, key_fn=lambda **kwargs: "pulse")
def get_market_pulse(request: Request) -> dict[str, Any]:
    return compute_overall_market_pulse()


@router.get("/sectors")
@limiter.limit(PUBLIC_RATE_LIMIT)
@cached(market_pulse_cache, key_fn=lambda **kwargs: "sectors")
def get_sector_performance(request: Request) -> list[dict[str, Any]]:
    return compute_sector_wise_pulse()


def build_price_and_signal_rows(symbols: list[str], company_names: dict[str, str]) -> list[dict[str, Any]]:
    if not symbols:
        return []

    with get_readonly_session() as session:
        price_rows = session.execute(
            text(
                """
                SELECT latest.symbol, latest.close AS latest_close, prev.close AS previous_close
                FROM (
                    SELECT DISTINCT ON (symbol) symbol, date, close
                    FROM daily_prices
                    WHERE symbol = ANY(:symbols)
                    ORDER BY symbol, date DESC
                ) latest
                LEFT JOIN LATERAL (
                    SELECT close FROM daily_prices p
                    WHERE p.symbol = latest.symbol AND p.date < latest.date
                    ORDER BY p.date DESC LIMIT 1
                ) prev ON true
                """
            ),
            {"symbols": symbols},
        ).all()
        prices_by_symbol = {row.symbol: (row.latest_close, row.previous_close) for row in price_rows}

        signal_rows = (
            session.execute(
                select(TechnicalSignal)
                .where(TechnicalSignal.symbol.in_(symbols))
                .where(TechnicalSignal.timeframe == SignalTimeframe.DAILY)
                .order_by(TechnicalSignal.symbol, TechnicalSignal.date.desc())
                .distinct(TechnicalSignal.symbol)
            )
            .scalars()
            .all()
        )
        session.expunge_all()

        liquidity_rows = session.execute(
            select(SymbolLiquidityTier.symbol, SymbolLiquidityTier.liquidity_tier).where(
                SymbolLiquidityTier.symbol.in_(symbols)
            )
        ).all()
        liquidity_by_symbol = {row.symbol: row.liquidity_tier for row in liquidity_rows}

        confidence_rows = (
            session.execute(select(SignalConfidence).where(SignalConfidence.signal_name.in_(TARGET_SIGNAL_HORIZONS)))
            .scalars()
            .all()
        )
        session.expunge_all()

    confidence_by_name = {row.signal_name: row for row in confidence_rows}
    signal_row_by_symbol = {row.symbol: row for row in signal_rows}

    results = []
    for symbol in symbols:
        latest_close, previous_close = prices_by_symbol.get(symbol, (None, None))
        percent_change = None
        if latest_close is not None and previous_close:
            percent_change = (latest_close - previous_close) / previous_close * 100

        active_signal = None
        signal_row = signal_row_by_symbol.get(symbol)
        if signal_row is not None and latest_close is not None:
            liquidity_tier = liquidity_by_symbol.get(symbol)
            evaluated = evaluate_target_signal_conditions(signal_row, latest_close, liquidity_tier, confidence_by_name)
            active = [s for s in evaluated if s["active"]]
            if active:
                active_signal = max(
                    active,
                    key=lambda s: s["avg_win_rate_minus_baseline"]
                    if s["avg_win_rate_minus_baseline"] is not None
                    else Decimal("-Infinity"),
                )

        results.append(
            {
                "symbol": symbol,
                "company_name": company_names[symbol],
                "latest_close": latest_close,
                "percent_change": percent_change,
                "active_signal": (
                    {
                        "signal_name": active_signal["signal_name"],
                        "tier": active_signal["tier"],
                        "avg_win_rate_minus_baseline": active_signal["avg_win_rate_minus_baseline"],
                    }
                    if active_signal is not None
                    else None
                ),
            }
        )

    return results


@router.get("/sectors/{sector}/stocks")
@limiter.limit(PUBLIC_RATE_LIMIT)
def get_sector_stocks(sector: str, request: Request) -> list[dict[str, Any]]:
    with get_readonly_session() as session:
        companies = session.execute(
            select(Company.symbol, Company.company_name)
            .where(Company.sector == sector)
            .where(Company.instrument_type == "Equity")
            .where(Company.status == "A")
            .order_by(Company.symbol)
        ).all()

    if not companies:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown sector: {sector}")

    symbols = [row.symbol for row in companies]
    company_names = {row.symbol: row.company_name for row in companies}

    results = build_price_and_signal_rows(symbols, company_names)
    results.sort(
        key=lambda r: r["percent_change"] if r["percent_change"] is not None else Decimal("-Infinity"),
        reverse=True,
    )
    return results


@router.get("/active-signals")
@limiter.limit(PUBLIC_RATE_LIMIT)
@cached(market_pulse_cache, key_fn=lambda **kwargs: "active-signals")
def get_active_signals(request: Request) -> dict[str, Any]:
    return compute_active_signals()


@router.get("/index-history")
@limiter.limit(PUBLIC_RATE_LIMIT)
@cached(index_history_cache, key_fn=lambda **kwargs: kwargs.get("range_"))
def get_index_history(request: Request, range_: str = Query("6M", alias="range")) -> list[dict[str, Any]]:
    if range_ not in VALID_RANGES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid range: {range_}")

    if range_ == "1D":
        return []

    with get_readonly_session() as session:
        query = select(MarketIndex).where(MarketIndex.index_name == NEPSE_INDEX_NAME)
        cutoff = range_cutoff(range_)
        if cutoff is not None:
            query = query.where(MarketIndex.date >= cutoff)
        rows = session.execute(query.order_by(MarketIndex.date.asc())).scalars().all()
        session.expunge_all()

    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No index history available")

    return [
        {"date": row.date, "open": row.open, "high": row.high, "low": row.low, "close": row.close}
        for row in rows
    ]


@router.get("/index-intraday-today")
@limiter.limit(PUBLIC_RATE_LIMIT)
@cached(intraday_today_cache, key_fn=lambda **kwargs: "index-intraday-today")
def get_index_intraday_today(request: Request) -> dict[str, Any]:
    today = date.today()
    with get_readonly_session() as session:
        rows = session.execute(
            select(IntradayIndexSnapshot)
            .where(IntradayIndexSnapshot.index_name == NEPSE_INDEX_NAME)
            .where(func.date(IntradayIndexSnapshot.snapshot_time) == today)
            .order_by(IntradayIndexSnapshot.snapshot_time.asc())
        ).scalars().all()
        session.expunge_all()

    points = [
        {
            "time": int(row.snapshot_time.astimezone(timezone.utc).timestamp()),
            "price": row.current_value,
        }
        for row in rows
        if row.current_value is not None
    ]

    return {"index_name": NEPSE_INDEX_NAME, "date": today, "has_data": bool(points), "points": points}
