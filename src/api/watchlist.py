from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.api.dependencies import get_current_user
from src.api.market import build_price_and_signal_rows
from src.database.connection import get_session
from src.database.models import Company, User, Watchlist

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


class WatchlistAdd(BaseModel):
    symbol: str


class ActiveSignal(BaseModel):
    signal_name: str
    tier: str | None
    avg_win_rate_minus_baseline: Decimal | None


class WatchlistItem(BaseModel):
    symbol: str
    company_name: str
    latest_close: Decimal | None
    percent_change: Decimal | None
    active_signal: ActiveSignal | None
    added_at: datetime


@router.post("", status_code=status.HTTP_201_CREATED)
def add_to_watchlist(payload: WatchlistAdd, current_user: User = Depends(get_current_user)) -> dict:
    symbol = payload.symbol.strip().upper()

    with get_session() as session:
        company = session.execute(select(Company).where(Company.symbol == symbol)).scalar_one_or_none()
        if company is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown symbol: {symbol}")

        entry = Watchlist(user_id=current_user.id, symbol=symbol, added_at=datetime.now(timezone.utc))
        session.add(entry)
        try:
            session.flush()
        except IntegrityError:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Symbol is already on your watchlist")
        session.expunge(entry)

    return {"symbol": entry.symbol, "added_at": entry.added_at}


@router.get("", response_model=list[WatchlistItem])
def list_watchlist(current_user: User = Depends(get_current_user)) -> list[WatchlistItem]:
    with get_session() as session:
        entries = session.execute(
            select(Watchlist).where(Watchlist.user_id == current_user.id).order_by(Watchlist.added_at.desc())
        ).scalars().all()
        session.expunge_all()

        symbols = [e.symbol for e in entries]
        if not symbols:
            return []

        companies = session.execute(select(Company.symbol, Company.company_name).where(Company.symbol.in_(symbols))).all()

    company_names = {row.symbol: row.company_name for row in companies}
    rows_by_symbol = {row["symbol"]: row for row in build_price_and_signal_rows(symbols, company_names)}
    added_at_by_symbol = {e.symbol: e.added_at for e in entries}

    return [
        WatchlistItem(
            symbol=symbol,
            company_name=company_names.get(symbol, symbol),
            latest_close=rows_by_symbol.get(symbol, {}).get("latest_close"),
            percent_change=rows_by_symbol.get(symbol, {}).get("percent_change"),
            active_signal=rows_by_symbol.get(symbol, {}).get("active_signal"),
            added_at=added_at_by_symbol[symbol],
        )
        for symbol in symbols
    ]


@router.delete("/{symbol}", status_code=status.HTTP_204_NO_CONTENT)
def remove_from_watchlist(symbol: str, current_user: User = Depends(get_current_user)) -> None:
    with get_session() as session:
        entry = session.execute(
            select(Watchlist)
            .where(Watchlist.user_id == current_user.id)
            .where(Watchlist.symbol == symbol.strip().upper())
        ).scalar_one_or_none()
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Symbol not found on watchlist")
        session.delete(entry)
