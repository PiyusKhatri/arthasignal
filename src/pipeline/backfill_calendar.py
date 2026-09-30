from __future__ import annotations

import bisect
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
WEEKEND_NAME = "Weekend"
HOLIDAY_NAME = "Holiday"
AUTOMATIC_NON_TRADING_NAMES = {WEEKEND_NAME, HOLIDAY_NAME}
WEEKEND_WEEKLY_OCCURRENCES = 4
RECENT_WEEKDAY_WINDOW_DAYS = 56
INDEX_CONTINUITY_MIN_TOLERANCE = 0.5
INDEX_CONTINUITY_RELATIVE_TOLERANCE = 0.001

IndexSeries = tuple[list[date], list[float], list[float]]


def _session_dates(start_date: date, end_date: date) -> set[date]:
    with get_session() as session:
        rows = session.execute(
            select(DailyPrice.date).distinct().where(DailyPrice.date >= start_date).where(DailyPrice.date <= end_date)
        ).all()
    return {row.date for row in rows}


def _confirmed_market_data_exists(day: date) -> bool:
    with get_session() as session:
        price_exists = session.execute(
            select(DailyPrice.id).where(DailyPrice.date == day).limit(1)
        ).scalar_one_or_none()
    return price_exists is not None


def _calendar_status(day: date) -> tuple[bool, str | None] | None:
    with get_session() as session:
        row = session.execute(
            select(TradingCalendar.is_trading_day, TradingCalendar.holiday_name).where(TradingCalendar.date == day)
        ).one_or_none()
    if row is None:
        return None
    return bool(row.is_trading_day), row.holiday_name


def _is_structural_weekend(day: date, sessions: set[date], last_session: date | None) -> bool:
    lookback_days = 7 * WEEKEND_WEEKLY_OCCURRENCES
    has_recent_sessions = any(day - timedelta(days=offset) in sessions for offset in range(1, lookback_days + 1))
    previous = [day - timedelta(days=7 * step) for step in range(1, WEEKEND_WEEKLY_OCCURRENCES + 1)]
    if has_recent_sessions and not any(candidate in sessions for candidate in previous):
        return True
    following = [day + timedelta(days=7 * step) for step in range(1, WEEKEND_WEEKLY_OCCURRENCES + 1)]
    if last_session is not None and following[-1] <= last_session:
        return not any(candidate in sessions for candidate in following)
    return False


def _load_index_series(start_date: date, end_date: date) -> dict[str, IndexSeries]:
    with get_session() as session:
        rows = session.execute(
            select(MarketIndex.index_name, MarketIndex.date, MarketIndex.close, MarketIndex.points_change)
            .where(MarketIndex.date >= start_date)
            .where(MarketIndex.date <= end_date)
            .order_by(MarketIndex.index_name, MarketIndex.date)
        ).all()
    series: dict[str, IndexSeries] = {}
    for index_name, row_date, close, points_change in rows:
        dates, closes, changes = series.setdefault(index_name, ([], [], []))
        dates.append(row_date)
        closes.append(float(close))
        changes.append(float(points_change))
    return series


def _verified_no_session(day: date, next_session: date | None, index_series: dict[str, IndexSeries]) -> bool:
    if next_session is None:
        return False
    no_session_votes = 0
    session_votes = 0
    for dates, closes, changes in index_series.values():
        position = bisect.bisect_left(dates, next_session)
        if position == 0 or position >= len(dates) or dates[position] != next_session:
            continue
        implied_previous_close = closes[position] - changes[position]
        tolerance = max(INDEX_CONTINUITY_MIN_TOLERANCE, INDEX_CONTINUITY_RELATIVE_TOLERANCE * abs(closes[position]))
        continuous = abs(implied_previous_close - closes[position - 1]) <= tolerance
        day_position = bisect.bisect_left(dates, day)
        has_day_row = day_position < len(dates) and dates[day_position] == day
        day_row_is_stale = not has_day_row or (day_position > 0 and closes[day_position] == closes[day_position - 1])
        if continuous and day_row_is_stale:
            no_session_votes += 1
        else:
            session_votes += 1
    return no_session_votes > 0 and no_session_votes > session_votes


def _existing_calendar_rows(start_date: date, end_date: date) -> dict[date, tuple[bool, str | None]]:
    with get_session() as session:
        rows = session.execute(
            select(TradingCalendar.date, TradingCalendar.is_trading_day, TradingCalendar.holiday_name)
            .where(TradingCalendar.date >= start_date)
            .where(TradingCalendar.date <= end_date)
        ).all()
    return {row.date: (bool(row.is_trading_day), row.holiday_name) for row in rows}


def classify_calendar_day(
    day: date,
    *,
    sessions: set[date],
    sorted_sessions: list[date],
    index_series: dict[str, IndexSeries],
    existing: tuple[bool, str | None] | None,
    end_date: date,
    attempt_confirmed_for_today: bool,
) -> dict[str, Any] | None:
    last_session = sorted_sessions[-1] if sorted_sessions else None

    if sorted_sessions and day < sorted_sessions[0]:
        return None

    if day in sessions:
        return {"date": day, "is_trading_day": True, "holiday_name": None, "is_known_holiday": False}

    if _is_structural_weekend(day, sessions, last_session):
        return {"date": day, "is_trading_day": False, "holiday_name": WEEKEND_NAME, "is_known_holiday": False}

    if existing is not None and existing[0] is False and existing[1] and existing[1] not in AUTOMATIC_NON_TRADING_NAMES:
        return {"date": day, "is_trading_day": False, "holiday_name": existing[1], "is_known_holiday": True}

    if last_session is not None and day < last_session:
        next_position = bisect.bisect_right(sorted_sessions, day)
        next_session = sorted_sessions[next_position] if next_position < len(sorted_sessions) else None
        if _verified_no_session(day, next_session, index_series):
            return {"date": day, "is_trading_day": False, "holiday_name": HOLIDAY_NAME, "is_known_holiday": True}
        return {"date": day, "is_trading_day": False, "holiday_name": None, "is_known_holiday": False}

    if day == end_date and not attempt_confirmed_for_today:
        return None

    return {"date": day, "is_trading_day": True, "holiday_name": None, "is_known_holiday": False}


def run_calendar_backfill(
    years: int = BACKFILL_YEARS,
    attempt_confirmed_for_today: bool = False,
) -> dict[str, Any]:
    start_time = time.perf_counter()

    end_date = date.today()
    start_date = end_date - timedelta(days=years * 365)
    lookback_start = start_date - timedelta(days=7 * WEEKEND_WEEKLY_OCCURRENCES)

    sessions = _session_dates(lookback_start, end_date)
    sorted_sessions = sorted(sessions)
    index_series = _load_index_series(lookback_start, end_date)
    existing_rows = _existing_calendar_rows(start_date, end_date)

    logger.info(
        "Found %d sessions with real daily_prices rows between %s and %s",
        len(sessions),
        lookback_start,
        end_date,
    )

    rows: list[dict[str, Any]] = []
    today_row_written = False
    repaired_unexplained_false_rows = 0

    current = start_date
    while current <= end_date:
        existing = existing_rows.get(current)
        row = classify_calendar_day(
            current,
            sessions=sessions,
            sorted_sessions=sorted_sessions,
            index_series=index_series,
            existing=existing,
            end_date=end_date,
            attempt_confirmed_for_today=attempt_confirmed_for_today,
        )
        if row is None:
            current += timedelta(days=1)
            continue
        if row["is_trading_day"] and existing is not None and existing[0] is False and existing[1] is None:
            repaired_unexplained_false_rows += 1
        rows.append(row)
        if current == end_date:
            today_row_written = True
        current += timedelta(days=1)

    rows_written = upsert_trading_calendar_rows(rows)

    elapsed_seconds = time.perf_counter() - start_time
    trading_day_count = sum(1 for row in rows if row["is_trading_day"])
    non_trading_day_count = len(rows) - trading_day_count
    session_count = sum(1 for row in rows if row["date"] in sessions)
    known_holiday_count = sum(1 for row in rows if row["is_known_holiday"])
    unexplained_non_trading_days = sum(
        1 for row in rows if not row["is_trading_day"] and row["holiday_name"] is None
    )

    summary = {
        "total_days_processed": len(rows),
        "rows_written": rows_written,
        "trading_days": trading_day_count,
        "sessions_with_prices": session_count,
        "non_trading_days": non_trading_day_count,
        "known_holidays": known_holiday_count,
        "unexplained_non_trading_days": unexplained_non_trading_days,
        "repaired_unexplained_false_rows": repaired_unexplained_false_rows,
        "today_row_written": today_row_written,
        "execution_time_seconds": round(elapsed_seconds, 2),
    }

    logger.info("Calendar backfill summary: %s", summary)

    return summary


def _known_non_trading_weekdays() -> set[int]:
    with get_session() as session:
        rows = session.execute(select(TradingCalendar.date, TradingCalendar.is_trading_day)).all()
    if not rows:
        return set()

    window_start = max(row.date for row in rows) - timedelta(days=RECENT_WEEKDAY_WINDOW_DAYS - 1)
    calendar_days_by_weekday: dict[int, int] = {i: 0 for i in range(7)}
    trading_days_by_weekday: dict[int, int] = {i: 0 for i in range(7)}
    for row in rows:
        if row.date < window_start:
            continue
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
