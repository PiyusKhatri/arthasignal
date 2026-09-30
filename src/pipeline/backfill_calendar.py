from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select

from src.database.connection import get_session
from src.database.models import DailyPrice, MarketIndex, TradingCalendar
from src.pipeline.db_writers import upsert_trading_calendar_rows

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BACKFILL_YEARS = 5
STRUCTURAL_WEEKEND_TRADING_RATIO_THRESHOLD = 0.2


def _confirmed_trading_dates(start_date: date, end_date: date) -> set[date]:
    with get_session() as session:
        price_dates = session.execute(
            select(DailyPrice.date).distinct().where(DailyPrice.date >= start_date).where(DailyPrice.date <= end_date)
        ).all()
        index_dates = session.execute(
            select(MarketIndex.date).distinct().where(MarketIndex.date >= start_date).where(MarketIndex.date <= end_date)
        ).all()
    return {row.date for row in price_dates} | {row.date for row in index_dates}


def _confirmed_market_data_exists(day: date) -> bool:
    with get_session() as session:
        price_exists = session.execute(
            select(DailyPrice.id).where(DailyPrice.date == day).limit(1)
        ).scalar_one_or_none()
        if price_exists is not None:
            return True
        index_exists = session.execute(
            select(MarketIndex.id).where(MarketIndex.date == day).limit(1)
        ).scalar_one_or_none()
    return index_exists is not None


def _calendar_status(day: date) -> tuple[bool, str | None] | None:
    with get_session() as session:
        row = session.execute(
            select(TradingCalendar.is_trading_day, TradingCalendar.holiday_name).where(TradingCalendar.date == day)
        ).one_or_none()
    if row is None:
        return None
    return bool(row.is_trading_day), row.holiday_name


def _derive_structural_weekend_weekdays(
    start_date: date,
    end_date: date,
    trading_dates: set[date],
) -> set[int]:
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


def _existing_calendar_rows(start_date: date, end_date: date) -> dict[date, tuple[bool, str | None]]:
    with get_session() as session:
        rows = session.execute(
            select(TradingCalendar.date, TradingCalendar.is_trading_day, TradingCalendar.holiday_name)
            .where(TradingCalendar.date >= start_date)
            .where(TradingCalendar.date <= end_date)
        ).all()
    return {row.date: (bool(row.is_trading_day), row.holiday_name) for row in rows}


def run_calendar_backfill(
    years: int = BACKFILL_YEARS,
    attempt_confirmed_for_today: bool = False,
) -> dict[str, Any]:
    start_time = time.perf_counter()

    end_date = date.today()
    start_date = end_date - timedelta(days=years * 365)

    confirmed_trading_dates = _confirmed_trading_dates(start_date, end_date)
    existing_rows = _existing_calendar_rows(start_date, end_date)

    logger.info(
        "Found %d confirmed trading days (daily_prices or market_index has real rows) between %s and %s",
        len(confirmed_trading_dates),
        start_date,
        end_date,
    )

    weekend_weekdays = _derive_structural_weekend_weekdays(start_date, end_date, confirmed_trading_dates)
    logger.info("Derived structural weekend weekdays (0=Monday): %s", sorted(weekend_weekdays))

    rows: list[dict[str, Any]] = []
    today_row_written = False
    repaired_unexplained_false_rows = 0

    current = start_date
    while current <= end_date:
        existing = existing_rows.get(current)

        if current in confirmed_trading_dates:
            is_trading_day = True
            holiday_name = None
        elif current.weekday() in weekend_weekdays:
            is_trading_day = False
            holiday_name = "Weekend"
        elif existing is not None and existing[0] is False and existing[1]:
            is_trading_day = False
            holiday_name = existing[1]
        elif current == end_date and not attempt_confirmed_for_today:
            logger.info(
                "Skipping calendar row for %s: no confirmed EOD data yet and no ingestion attempt was requested",
                current,
            )
            current += timedelta(days=1)
            continue
        else:
            is_trading_day = True
            holiday_name = None
            if existing is not None and existing[0] is False and existing[1] is None:
                repaired_unexplained_false_rows += 1

        rows.append(
            {
                "date": current,
                "is_trading_day": is_trading_day,
                "holiday_name": holiday_name,
            }
        )
        if current == end_date:
            today_row_written = True
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
        "repaired_unexplained_false_rows": repaired_unexplained_false_rows,
        "today_row_written": today_row_written,
        "execution_time_seconds": round(elapsed_seconds, 2),
    }

    logger.info(
        "Calendar backfill summary: total_days_processed=%d rows_written=%d trading_days=%d "
        "non_trading_days=%d unexplained_non_trading_days=%d repaired_unexplained_false_rows=%d "
        "today_row_written=%s execution_time_seconds=%.2f",
        summary["total_days_processed"],
        summary["rows_written"],
        summary["trading_days"],
        summary["non_trading_days"],
        summary["unexplained_non_trading_days"],
        summary["repaired_unexplained_false_rows"],
        summary["today_row_written"],
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


def is_trading_day(day: date | None = None) -> bool:
    """Return whether *day* is a trading day, not whether NEPSE is open right now."""

    day = day or date.today()

    if _confirmed_market_data_exists(day):
        return True

    status = _calendar_status(day)
    if status is not None:
        is_trading, holiday_name = status
        if is_trading:
            return True
        if holiday_name:
            return False
        logger.warning(
            "Trading calendar has an unexplained non-trading row for %s; treating it as untrusted and falling back "
            "to the structural weekday pattern",
            day,
        )

    non_trading_weekdays = _known_non_trading_weekdays()
    result = day.weekday() not in non_trading_weekdays
    logger.warning(
        "Trading-day fallback for %s: weekday=%d known_non_trading_weekdays=%s -> is_trading_day=%s",
        day,
        day.weekday(),
        sorted(non_trading_weekdays),
        result,
    )
    return result


def is_market_open_today() -> bool:
    """Backward-compatible alias for callers that really mean 'is today a trading day?'."""

    return is_trading_day(date.today())


if __name__ == "__main__":
    run_calendar_backfill()
