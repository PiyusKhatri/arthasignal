from __future__ import annotations

import bisect
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update

from src.database.connection import get_session
from src.database.models import (
    Company,
    CorporateAction,
    DailyPrice,
    SignalCall,
    SignalCallOutcome,
    SignalCallStatus,
    TradingCalendar,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

VOID_SEARCH_CAP_TRADING_DAYS = 3


def _load_trading_days() -> list[date]:
    with get_session() as session:
        rows = session.execute(
            select(TradingCalendar.date)
            .where(TradingCalendar.is_trading_day.is_(True))
            .order_by(TradingCalendar.date)
        ).scalars().all()
    return list(rows)


def _resolution_target_date(entry_date: date, horizon: int, trading_days: list[date]) -> date | None:
    start = bisect.bisect_right(trading_days, entry_date)
    target_idx = start + horizon - 1
    if target_idx >= len(trading_days):
        return None
    return trading_days[target_idx]


def _void_cutoff_date(target_date: date, trading_days: list[date]) -> date | None:
    idx = bisect.bisect_left(trading_days, target_date)
    cutoff_idx = idx + VOID_SEARCH_CAP_TRADING_DAYS
    if cutoff_idx >= len(trading_days):
        return None
    return trading_days[cutoff_idx]


def _load_pending_calls() -> list[SignalCall]:
    with get_session() as session:
        rows = session.execute(
            select(SignalCall).where(SignalCall.status == SignalCallStatus.PENDING)
        ).scalars().all()
        session.expunge_all()
    return rows


def _load_close_price_index(symbols: set[str], start_date: date, end_date: date) -> dict[str, tuple[list[date], list[Decimal]]]:
    if not symbols:
        return {}
    with get_session() as session:
        rows = session.execute(
            select(DailyPrice.symbol, DailyPrice.date, DailyPrice.close)
            .where(DailyPrice.symbol.in_(symbols))
            .where(DailyPrice.date >= start_date)
            .where(DailyPrice.date <= end_date)
            .order_by(DailyPrice.symbol, DailyPrice.date)
        ).all()

    index: dict[str, tuple[list[date], list[Decimal]]] = {}
    current_symbol = None
    dates: list[date] = []
    closes: list[Decimal] = []
    for symbol, entry_date, close in rows:
        if symbol != current_symbol:
            if current_symbol is not None:
                index[current_symbol] = (dates, closes)
            current_symbol = symbol
            dates, closes = [], []
        dates.append(entry_date)
        closes.append(close)
    if current_symbol is not None:
        index[current_symbol] = (dates, closes)
    return index


def _find_resolution_price(
    close_index: dict[str, tuple[list[date], list[Decimal]]],
    symbol: str,
    target_date: date,
    cutoff_date: date | None,
) -> tuple[date, Decimal] | None:
    series = close_index.get(symbol)
    if series is None:
        return None
    dates, closes = series
    pos = bisect.bisect_left(dates, target_date)
    if pos >= len(dates):
        return None
    found_date = dates[pos]
    if cutoff_date is not None and found_date > cutoff_date:
        return None
    return found_date, closes[pos]


def _load_corporate_action_symbols(symbols: set[str], start_date: date, end_date: date) -> set[str]:
    if not symbols:
        return set()
    with get_session() as session:
        rows = session.execute(
            select(CorporateAction.symbol)
            .where(CorporateAction.symbol.in_(symbols))
            .where(CorporateAction.action_date > start_date)
            .where(CorporateAction.action_date <= end_date)
        ).scalars().all()
    return set(rows)


def _load_company_status(symbols: set[str]) -> dict[str, str]:
    if not symbols:
        return {}
    with get_session() as session:
        rows = session.execute(
            select(Company.symbol, Company.status).where(Company.symbol.in_(symbols))
        ).all()
    return {r.symbol: r.status for r in rows}


def _apply_updates(resolved: list[dict[str, Any]], voided: list[dict[str, Any]]) -> int:
    updated = 0
    with get_session() as session:
        for group, status in ((resolved, SignalCallStatus.RESOLVED), (voided, SignalCallStatus.VOID)):
            for entry in group:
                values = {
                    "status": status,
                    "outcome": entry["outcome"],
                    "resolution_date": entry.get("resolution_date"),
                    "resolution_price": entry.get("resolution_price"),
                }
                session.execute(update(SignalCall).where(SignalCall.id == entry["id"]).values(**values))
                updated += 1
    return updated


def grade_signal_calls(as_of: date | None = None) -> dict[str, Any]:
    as_of = as_of or date.today()
    trading_days = _load_trading_days()
    pending_calls = _load_pending_calls()

    ready: list[dict[str, Any]] = []
    not_ready_count = 0

    for call in pending_calls:
        target_date = _resolution_target_date(call.entry_date, call.forward_days_horizon, trading_days)
        if target_date is None or target_date > as_of:
            not_ready_count += 1
            continue
        cutoff_date = _void_cutoff_date(target_date, trading_days)
        ready.append({"call": call, "target_date": target_date, "cutoff_date": cutoff_date})

    if not ready:
        summary = {
            "total_pending": len(pending_calls),
            "not_ready": not_ready_count,
            "resolved": 0,
            "voided": 0,
            "win": 0,
            "loss": 0,
        }
        logger.info("Signal call grading summary: %s", summary)
        return summary

    symbols = {entry["call"].symbol for entry in ready}
    min_target = min(entry["target_date"] for entry in ready)
    max_cutoff = max((entry["cutoff_date"] or entry["target_date"]) for entry in ready)
    close_index = _load_close_price_index(symbols, min_target, max_cutoff)

    entry_dates = {entry["call"].entry_date for entry in ready}
    min_entry_date = min(entry_dates)
    corporate_action_symbols = _load_corporate_action_symbols(symbols, min_entry_date, max_cutoff)
    company_status = _load_company_status(symbols)

    resolved_rows: list[dict[str, Any]] = []
    voided_rows: list[dict[str, Any]] = []
    win_count = 0
    loss_count = 0
    void_notes: list[str] = []

    for entry in ready:
        call = entry["call"]
        found = _find_resolution_price(close_index, call.symbol, entry["target_date"], entry["cutoff_date"])

        if found is None:
            status_note = company_status.get(call.symbol, "unknown")
            void_notes.append(
                f"{call.symbol}/{call.signal_name}/{call.entry_date}: no trade within "
                f"{VOID_SEARCH_CAP_TRADING_DAYS} trading days of resolution target {entry['target_date']} "
                f"(company status={status_note})"
            )
            voided_rows.append({"id": call.id, "outcome": SignalCallOutcome.VOID})
            continue

        resolution_date, resolution_price = found
        entry_price = Decimal(str(call.entry_price))
        resolution_price = Decimal(str(resolution_price))

        if call.symbol in corporate_action_symbols:
            void_notes.append(
                f"{call.symbol}/{call.signal_name}/{call.entry_date}: corporate action occurred between "
                f"entry and resolution - raw price comparison may be distorted, resolved anyway per raw-basis policy"
            )

        outcome = SignalCallOutcome.WIN if resolution_price > entry_price else SignalCallOutcome.LOSS
        if outcome == SignalCallOutcome.WIN:
            win_count += 1
        else:
            loss_count += 1

        resolved_rows.append(
            {
                "id": call.id,
                "outcome": outcome,
                "resolution_date": resolution_date,
                "resolution_price": resolution_price,
            }
        )

    updated = _apply_updates(resolved_rows, voided_rows)

    for note in void_notes:
        logger.warning("Signal call data-quality note: %s", note)

    summary = {
        "total_pending": len(pending_calls),
        "not_ready": not_ready_count,
        "resolved": len(resolved_rows),
        "voided": len(voided_rows),
        "win": win_count,
        "loss": loss_count,
        "rows_updated": updated,
    }
    logger.info("Signal call grading summary: %s", summary)
    return summary


if __name__ == "__main__":
    grade_signal_calls()
