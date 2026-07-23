from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select

from src.database.connection import get_session
from src.database.models import MarketIndex, TradingCalendar
from src.pipeline.db_writers import upsert_trading_calendar_rows
from src.scrapers import nepse_api

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BACKFILL_YEARS = 5
REFERENCE_INDEX_NAME = "NEPSE Index"
STRUCTURAL_WEEKEND_TRADING_RATIO_THRESHOLD = 0.2


def _trading_dates_from_market_index(start_date: date, end_date: date) -> set[date]:
    with get_session() as session:
        rows = session.execute(
            select(MarketIndex.date)
            .where(MarketIndex.index_name == REFERENCE_INDEX_NAME)
            .where(MarketIndex.date >= start_date)
            .where(MarketIndex.date <= end_date)
        ).all()
    return {row.date for row in rows}


def _derive_structural_weekend_weekdays(start_date: date, end_date: date, trading_dates: set[date]) -> set[int]:
    calendar_days_by_weekday: dict[int, int] = {i: 0 for i in range(7)}
    trading_days_by_weekday: dict[int, int] = {i: 0 for i in range(7)}

    current = start_date
    while current <= end_date:
        weekday = current.weekday()
        calendar_days_by_weekday[weekday] += 1
        if current in trading_dates:
            trading_days_by_weekday[weekday] += 1
        current += timedelta(days=1)

    weekend_weekdays = set()
    for weekday, total in calendar_days_by_weekday.items():
        ratio = trading_days_by_weekday[weekday] / total if total else 0
        if ratio < STRUCTURAL_WEEKEND_TRADING_RATIO_THRESHOLD:
            weekend_weekdays.add(weekday)
    return weekend_weekdays


def run_calendar_backfill(years: int = BACKFILL_YEARS) -> dict[str, Any]:
    start_time = time.perf_counter()

    end_date = date.today()
    start_date = end_date - timedelta(days=years * 365)

    trading_dates = _trading_dates_from_market_index(start_date, end_date)
    logger.info("Found %d trading days in market_index between %s and %s", len(trading_dates), start_date, end_date)

    weekend_weekdays = _derive_structural_weekend_weekdays(start_date, end_date, trading_dates)
    logger.info("Derived structural weekend weekdays (0=Monday): %s", sorted(weekend_weekdays))

    rows = []
    current = start_date
    while current <= end_date:
        is_trading_day = current in trading_dates
        holiday_name = None
        if not is_trading_day:
            holiday_name = "Weekend" if current.weekday() in weekend_weekdays else None
        rows.append(
            {
                "date": current,
                "is_trading_day": is_trading_day,
                "holiday_name": holiday_name,
            }
        )
        current += timedelta(days=1)

    rows_written = upsert_trading_calendar_rows(rows)

    elapsed_seconds = time.perf_counter() - start_time
    trading_day_count = sum(1 for row in rows if row["is_trading_day"])
    non_trading_day_count = len(rows) - trading_day_count
    unexplained_non_trading_days = sum(
        1 for row in rows if not row["is_trading_day"] and row["holiday_name"] is None
    )

    summary = {
        "total_days_processed": len(rows),
        "rows_written": rows_written,
        "trading_days": trading_day_count,
        "non_trading_days": non_trading_day_count,
        "unexplained_non_trading_days": unexplained_non_trading_days,
        "execution_time_seconds": round(elapsed_seconds, 2),
    }

    logger.info(
        "Calendar backfill summary: total_days_processed=%d rows_written=%d trading_days=%d "
        "non_trading_days=%d unexplained_non_trading_days=%d execution_time_seconds=%.2f",
        summary["total_days_processed"],
        summary["rows_written"],
        summary["trading_days"],
        summary["non_trading_days"],
        summary["unexplained_non_trading_days"],
        summary["execution_time_seconds"],
    )

    return summary


def _known_non_trading_weekdays() -> set[int]:
    with get_session() as session:
        rows = session.execute(select(TradingCalendar.date, TradingCalendar.is_trading_day)).all()

    calendar_days_by_weekday: dict[int, int] = {i: 0 for i in range(7)}
    trading_days_by_weekday: dict[int, int] = {i: 0 for i in range(7)}
    for row in rows:
        weekday = row.date.weekday()
        calendar_days_by_weekday[weekday] += 1
        if row.is_trading_day:
            trading_days_by_weekday[weekday] += 1

    non_trading_weekdays = set()
    for weekday, total in calendar_days_by_weekday.items():
        if total == 0:
            continue
        ratio = trading_days_by_weekday[weekday] / total
        if ratio < STRUCTURAL_WEEKEND_TRADING_RATIO_THRESHOLD:
            non_trading_weekdays.add(weekday)
    return non_trading_weekdays


def is_market_open_today() -> bool:
    today = date.today()
    with get_session() as session:
        row = session.execute(
            select(TradingCalendar.is_trading_day).where(TradingCalendar.date == today)
        ).scalar_one_or_none()

    if row is not None:
        return row

    logger.warning("No trading calendar entry for %s, falling back to live market status check", today)
    try:
        return nepse_api.is_market_open()
    except Exception:
        logger.exception("Live market status check failed for %s, falling back to a non-crashing signal", today)

        with get_session() as session:
            has_market_data_today = (
                session.execute(select(MarketIndex.id).where(MarketIndex.date == today).limit(1)).first()
                is not None
            )

        if has_market_data_today:
            logger.warning(
                "market_index already has a row for %s despite the live check failing, treating today as a trading day",
                today,
            )
            return True

        non_trading_weekdays = _known_non_trading_weekdays()
        fallback_result = today.weekday() not in non_trading_weekdays
        logger.warning(
            "No market_index data for %s either, falling back to historical weekday pattern "
            "(0=Monday): weekday=%d known_non_trading_weekdays=%s -> is_trading_day=%s",
            today,
            today.weekday(),
            sorted(non_trading_weekdays),
            fallback_result,
        )
        return fallback_result


if __name__ == "__main__":
    run_calendar_backfill()
