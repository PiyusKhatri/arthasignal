from __future__ import annotations

import bisect
import logging
from datetime import date
from decimal import Decimal
from statistics import mean
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
from src.pipeline.signal_validation_policy import VALIDATION_POLICY_VERSION

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


def _load_close_price_index(
    symbols: set[str], start_date: date, end_date: date
) -> dict[str, tuple[list[date], list[Decimal]]]:
    if not symbols or end_date < start_date:
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
    effective_cutoff: date,
) -> tuple[date, Decimal] | None:
    series = close_index.get(symbol)
    if series is None:
        return None
    dates, closes = series
    pos = bisect.bisect_left(dates, target_date)
    if pos >= len(dates):
        return None
    found_date = dates[pos]
    if found_date > effective_cutoff:
        return None
    return found_date, closes[pos]


def _load_corporate_action_dates(
    symbols: set[str], start_date: date, end_date: date
) -> dict[str, list[date]]:
    if not symbols:
        return {}
    with get_session() as session:
        rows = session.execute(
            select(CorporateAction.symbol, CorporateAction.action_date)
            .where(CorporateAction.symbol.in_(symbols))
            .where(CorporateAction.action_date > start_date)
            .where(CorporateAction.action_date <= end_date)
            .order_by(CorporateAction.symbol, CorporateAction.action_date)
        ).all()

    index: dict[str, list[date]] = {}
    for symbol, action_date in rows:
        index.setdefault(symbol, []).append(action_date)
    return index


def _has_corporate_action(
    action_dates: dict[str, list[date]], symbol: str, entry_date: date, resolution_date: date
) -> bool:
    dates = action_dates.get(symbol, [])
    pos = bisect.bisect_right(dates, entry_date)
    return pos < len(dates) and dates[pos] <= resolution_date


def _load_company_status(symbols: set[str]) -> dict[str, str]:
    if not symbols:
        return {}
    with get_session() as session:
        rows = session.execute(select(Company.symbol, Company.status).where(Company.symbol.in_(symbols))).all()
    return {r.symbol: r.status for r in rows}


def _apply_updates(resolved: list[dict[str, Any]], voided: list[dict[str, Any]]) -> int:
    updated = 0
    with get_session() as session:
        for group, status_value in ((resolved, SignalCallStatus.RESOLVED), (voided, SignalCallStatus.VOID)):
            for entry in group:
                values = {
                    "status": status_value,
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
            "policy_version": VALIDATION_POLICY_VERSION,
            "total_pending": len(pending_calls),
            "not_ready": not_ready_count,
            "resolved": 0,
            "voided": 0,
            "win": 0,
            "loss": 0,
            "mean_gross_return_percent": None,
        }
        logger.info("Signal call grading summary: %s", summary)
        return summary

    symbols = {entry["call"].symbol for entry in ready}
    min_target = min(entry["target_date"] for entry in ready)
    close_index = _load_close_price_index(symbols, min_target, as_of)

    min_entry_date = min(entry["call"].entry_date for entry in ready)
    corporate_action_dates = _load_corporate_action_dates(symbols, min_entry_date, as_of)
    company_status = _load_company_status(symbols)

    resolved_rows: list[dict[str, Any]] = []
    voided_rows: list[dict[str, Any]] = []
    win_count = 0
    loss_count = 0
    data_quality_notes: list[str] = []
    gross_returns: list[float] = []

    for entry in ready:
        call = entry["call"]
        cutoff_date = entry["cutoff_date"]
        effective_cutoff = min(cutoff_date, as_of) if cutoff_date is not None else as_of
        found = _find_resolution_price(close_index, call.symbol, entry["target_date"], effective_cutoff)

        if found is None:
            # The +3-trading-day search window has not elapsed yet. Keep the
            # call pending rather than prematurely declaring it VOID.
            if cutoff_date is None or cutoff_date > as_of:
                not_ready_count += 1
                continue

            status_note = company_status.get(call.symbol, "unknown")
            data_quality_notes.append(
                f"{call.symbol}/{call.signal_name}/{call.entry_date}: no trade within "
                f"{VOID_SEARCH_CAP_TRADING_DAYS} trading days of resolution target {entry['target_date']} "
                f"(company status={status_note})"
            )
            voided_rows.append({"id": call.id, "outcome": SignalCallOutcome.VOID})
            continue

        resolution_date, resolution_price_raw = found
        if _has_corporate_action(corporate_action_dates, call.symbol, call.entry_date, resolution_date):
            data_quality_notes.append(
                f"{call.symbol}/{call.signal_name}/{call.entry_date}: corporate action occurred between "
                "entry and resolution; call voided to avoid grading a distorted raw-price move"
            )
            voided_rows.append({"id": call.id, "outcome": SignalCallOutcome.VOID})
            continue

        entry_price = Decimal(str(call.entry_price))
        resolution_price = Decimal(str(resolution_price_raw))
        gross_return_percent = float((resolution_price / entry_price - Decimal("1")) * Decimal("100"))
        gross_returns.append(gross_return_percent)

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
                "gross_return_percent": gross_return_percent,
            }
        )

    updated = _apply_updates(resolved_rows, voided_rows)

    for note in data_quality_notes:
        logger.warning("Signal call data-quality note: %s", note)

    summary = {
        "policy_version": VALIDATION_POLICY_VERSION,
        "total_pending": len(pending_calls),
        "not_ready": not_ready_count,
        "resolved": len(resolved_rows),
        "voided": len(voided_rows),
        "win": win_count,
        "loss": loss_count,
        "mean_gross_return_percent": round(mean(gross_returns), 6) if gross_returns else None,
        "rows_updated": updated,
    }
    logger.info("Signal call grading summary: %s", summary)
    return summary


if __name__ == "__main__":
    grade_signal_calls()
