from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select, text

from src.api.cache import cached, market_pulse_cache
from src.api.db_readonly import get_readonly_session
from src.api.rate_limit import PUBLIC_RATE_LIMIT, limiter
from src.api.stocks import evaluate_target_signal_conditions
from src.database.models import Company, SignalConfidence, SignalTimeframe, SymbolLiquidityTier, TechnicalSignal
from src.pipeline.extract_signal_calls import TARGET_SIGNAL_HORIZONS
from src.pipeline.market_pulse import compute_active_signals, compute_overall_market_pulse, compute_sector_wise_pulse

router = APIRouter(prefix="/market", tags=["market"])


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

    company_names = {row.symbol: row.company_name for row in companies}
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
