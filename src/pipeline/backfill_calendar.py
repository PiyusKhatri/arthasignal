from __future__ import annotations

import logging
import os
import time
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select

from src.database.connection import get_session
from src.database.models import DailyPrice, MarketIndex, TradingCalendar
from src.pipeline.db_writers import upsert_trading_calendar_rows
from src.scrapers import nepse_api

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


def run_calendar_backfill(years: int = BACKFILL_YEARS, attempt_confirmed_for_today: bool = False) -> dict[str, Any]:
    start_time = time.perf_counter()

    end_date = date.today()
    start_date = end_date - timedelta(days=years * 365)

    confirmed_trading_dates = _confirmed_trading_dates(start_date, end_date)
    logger.info(
        "Found %d confirmed trading days (daily_prices or market_index has real rows) between %s and %s",
        len(confirmed_trading_dates),
        start_date,
        end_date,
    )

    weekend_weekdays = _derive_structural_weekend_weekdays(start_date, end_date, confirmed_trading_dates)
    logger.info("Derived structural weekend weekdays (0=Monday): %s", sorted(weekend_weekdays))

    rows = []
    today_row_written = False
    current = start_date
    while current <= end_date:
        confirmed_trading = current in confirmed_trading_dates

        if current == end_date and not confirmed_trading and not attempt_confirmed_for_today:
            logger.info(
                "Skipping calendar row for %s: no confirmed EOD data yet and no genuine ingestion attempt "
                "was confirmed for today - refusing to preemptively mark it non-trading",
                current,
            )
            current += timedelta(days=1)
            continue

        is_trading_day = confirmed_trading
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
        if current == end_date:
            today_row_written = True
        current += timedelta(days=1)

    # Hard invariant, enforced independently of the loop above: a date with confirmed real
    # ingested rows must never be written as non-trading, no matter what the derived logic decided.
    for row in rows:
        if row["date"] in confirmed_trading_dates and not row["is_trading_day"]:
            row["is_trading_day"] = True
            row["holiday_name"] = None

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
        "today_row_written": today_row_written,
        "execution_time_seconds": round(elapsed_seconds, 2),
    }

    logger.info(
        "Calendar backfill summary: total_days_processed=%d rows_written=%d trading_days=%d "
        "non_trading_days=%d unexplained_non_trading_days=%d today_row_written=%s execution_time_seconds=%.2f",
        summary["total_days_processed"],
        summary["rows_written"],
        summary["trading_days"],
        summary["non_trading_days"],
        summary["unexplained_non_trading_days"],
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


def _in_ci() -> bool:
    return os.environ.get("GITHUB_ACTIONS", "").lower() == "true"


def is_market_open_today() -> bool:
    today = date.today()

    with get_session() as session:
        row = session.execute(
            select(TradingCalendar.is_trading_day).where(TradingCalendar.date == today)
        ).scalar_one_or_none()

    if row is not None:
        return row

    logger.warning("No trading calendar entry for %s, falling back to historical weekday pattern", today)
    non_trading_weekdays = _known_non_trading_weekdays()
    weekday_pattern_result = today.weekday() not in non_trading_weekdays
    logger.warning(
        "Historical weekday pattern (0=Monday): weekday=%d known_non_trading_weekdays=%s -> is_trading_day=%s",
        today.weekday(),
        sorted(non_trading_weekdays),
        weekday_pattern_result,
    )

    if _in_ci():
        logger.warning(
            "Running in CI (GITHUB_ACTIONS=true) - nepalstock.com blocks non-Nepal IPs so the live "
            "market-status check is known to be unreliable here; using the weekday-pattern result "
            "without attempting it, rather than risking a live-check exception silently resolving to closed"
        )
        return weekday_pattern_result

    logger.info("Not in CI, attempting live market status check as a last-resort refinement for %s", today)
    try:
        return nepse_api.is_market_open()
    except Exception:
        logger.exception(
            "Live market status check failed for %s - falling back to the weekday-pattern result (%s) "
            "instead of defaulting to closed",
            today,
            weekday_pattern_result,
        )
        return weekday_pattern_result


if __name__ == "__main__":
    run_calendar_backfill()
