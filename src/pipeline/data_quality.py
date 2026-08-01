from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text

from src.database.connection import get_session
from src.pipeline.backfill_calendar import is_market_open_today
from src.scrapers.symbols import get_all_listed_symbols

logger = logging.getLogger(__name__)

ROW_COUNT_TRAILING_DAYS = 10
ROW_COUNT_MIN_RATIO = 0.7
MISSING_SYMBOL_CONSECUTIVE_DAYS = 3
CIRCUIT_BREAKER_RULE_CHANGE_DATE = date(2026, 4, 20)
CIRCUIT_BREAKER_PERCENT_BEFORE_CHANGE = 10.0
CIRCUIT_BREAKER_PERCENT_ON_OR_AFTER_CHANGE = 15.0
CIRCUIT_BREAKER_FLAG_BUFFER_PERCENT = 2.0
STALE_FUNDAMENTALS_SYMBOL_DAYS = 45
STALE_FUNDAMENTALS_TABLE_DAYS = 14
STALE_SECTOR_FUNDAMENTAL_BASELINE_DAYS = 14
SIGNAL_CALL_EXTRACTION_STALL_CONSECUTIVE_DAYS = 3


def _circuit_breaker_flag_threshold(as_of_date: date) -> float:
    base = (
        CIRCUIT_BREAKER_PERCENT_ON_OR_AFTER_CHANGE
        if as_of_date >= CIRCUIT_BREAKER_RULE_CHANGE_DATE
        else CIRCUIT_BREAKER_PERCENT_BEFORE_CHANGE
    )
    return base + CIRCUIT_BREAKER_FLAG_BUFFER_PERCENT


def _latest_price_date():
    with get_session() as session:
        return session.execute(text("SELECT MAX(date) FROM daily_prices")).scalar()


def _trailing_trading_days(before_date, count):
    with get_session() as session:
        rows = session.execute(
            text(
                """
                SELECT date FROM trading_calendar
                WHERE is_trading_day AND date < :before_date
                ORDER BY date DESC LIMIT :count
                """
            ),
            {"before_date": before_date, "count": count},
        ).all()
    return [row.date for row in rows]


def _check_row_count_anomaly(latest_date) -> dict[str, Any]:
    trailing_days = _trailing_trading_days(latest_date, ROW_COUNT_TRAILING_DAYS)
    if not trailing_days:
        logger.warning("data_quality: no trailing trading days available, skipping row count check")
        return {"flagged": False}

    with get_session() as session:
        today_count = session.execute(
            text("SELECT COUNT(*) FROM daily_prices WHERE date = :d"), {"d": latest_date}
        ).scalar()
        trailing_counts = session.execute(
            text("SELECT COUNT(*) FROM daily_prices WHERE date = ANY(:days)"),
            {"days": trailing_days},
        ).scalar()

    trailing_average = trailing_counts / len(trailing_days)
    threshold = trailing_average * ROW_COUNT_MIN_RATIO
    flagged = today_count < threshold

    if flagged:
        logger.warning(
            "data_quality: row count anomaly on %s - got %d rows, trailing %d-day average is %.1f (threshold %.1f)",
            latest_date,
            today_count,
            len(trailing_days),
            trailing_average,
            threshold,
        )

    return {
        "flagged": flagged,
        "date": latest_date,
        "today_count": today_count,
        "trailing_average": round(trailing_average, 1),
        "threshold": round(threshold, 1),
    }


def _active_equity_symbols_from_companies_table() -> set[str]:
    with get_session() as session:
        rows = session.execute(
            text("SELECT symbol FROM companies WHERE status = 'A' AND instrument_type = 'Equity'")
        ).all()
    return {row.symbol for row in rows}


def _check_missing_symbols(latest_date) -> dict[str, Any]:
    listed_symbols = set(get_all_listed_symbols())
    equity_symbols = _active_equity_symbols_from_companies_table()
    active_symbols = listed_symbols & equity_symbols
    recent_days = _trailing_trading_days(latest_date + timedelta(days=1), MISSING_SYMBOL_CONSECUTIVE_DAYS)

    if len(recent_days) < MISSING_SYMBOL_CONSECUTIVE_DAYS:
        logger.warning("data_quality: fewer than %d trading days on record, skipping missing symbol check", MISSING_SYMBOL_CONSECUTIVE_DAYS)
        return {"flagged": False}

    with get_session() as session:
        present_by_day = {}
        for day in recent_days:
            rows = session.execute(
                text("SELECT symbol FROM daily_prices WHERE date = :d"), {"d": day}
            ).all()
            present_by_day[day] = {row.symbol for row in rows}

    missing_all_days = active_symbols
    for day in recent_days:
        missing_all_days = missing_all_days & (active_symbols - present_by_day[day])

    if missing_all_days:
        logger.warning(
            "data_quality: %d active symbols missing for %d+ consecutive trading days (%s to %s): %s",
            len(missing_all_days),
            MISSING_SYMBOL_CONSECUTIVE_DAYS,
            min(recent_days),
            max(recent_days),
            sorted(missing_all_days),
        )

    return {
        "flagged": bool(missing_all_days),
        "checked_days": recent_days,
        "missing_symbols": sorted(missing_all_days),
    }


def _check_price_sanity(latest_date) -> dict[str, Any]:
    with get_session() as session:
        rows = session.execute(
            text(
                """
                SELECT dp.symbol, dp.close, prev.close AS prev_close
                FROM daily_prices dp
                JOIN LATERAL (
                    SELECT close FROM daily_prices p
                    WHERE p.symbol = dp.symbol AND p.date < dp.date
                    ORDER BY p.date DESC LIMIT 1
                ) prev ON true
                WHERE dp.date = :d AND prev.close > 0
                """
            ),
            {"d": latest_date},
        ).all()

    flag_threshold = _circuit_breaker_flag_threshold(latest_date)

    violations = []
    for row in rows:
        pct_change = abs(float(row.close) - float(row.prev_close)) / float(row.prev_close) * 100
        if pct_change > flag_threshold:
            violations.append(
                {
                    "symbol": row.symbol,
                    "close": float(row.close),
                    "prev_close": float(row.prev_close),
                    "pct_change": round(pct_change, 2),
                }
            )

    if violations:
        logger.warning(
            "data_quality: %d rows on %s exceed the %.1f%% price sanity threshold: %s",
            len(violations),
            latest_date,
            flag_threshold,
            violations,
        )

    return {
        "flagged": bool(violations),
        "date": latest_date,
        "threshold": flag_threshold,
        "violations": violations,
    }


def _check_null_or_zero_prices(latest_date) -> dict[str, Any]:
    with get_session() as session:
        rows = session.execute(
            text(
                """
                SELECT symbol, open, high, low, close FROM daily_prices
                WHERE date = :d
                  AND (open IS NULL OR open = 0 OR high IS NULL OR high = 0
                       OR low IS NULL OR low = 0 OR close IS NULL OR close = 0)
                """
            ),
            {"d": latest_date},
        ).all()

    bad_rows = [
        {"symbol": row.symbol, "open": row.open, "high": row.high, "low": row.low, "close": row.close}
        for row in rows
    ]

    if bad_rows:
        logger.warning(
            "data_quality: %d rows on %s have a null or zero OHLC field: %s",
            len(bad_rows),
            latest_date,
            bad_rows,
        )

    return {"flagged": bool(bad_rows), "date": latest_date, "bad_rows": bad_rows}


def _check_fundamentals_symbol_staleness(latest_date) -> dict[str, Any]:
    equity_symbols = _active_equity_symbols_from_companies_table()

    with get_session() as session:
        rows = session.execute(text("SELECT symbol, max(reported_date) AS reported_date FROM fundamentals GROUP BY symbol")).all()
    latest_by_symbol = {row.symbol: row.reported_date for row in rows}

    stale_symbols = []
    for symbol in equity_symbols:
        reported_date = latest_by_symbol.get(symbol)
        if reported_date is None:
            stale_symbols.append({"symbol": symbol, "reported_date": None, "days_stale": None})
            continue
        days_stale = (latest_date - reported_date).days
        if days_stale > STALE_FUNDAMENTALS_SYMBOL_DAYS:
            stale_symbols.append({"symbol": symbol, "reported_date": reported_date, "days_stale": days_stale})

    if stale_symbols:
        logger.warning(
            "data_quality: %d active equity symbols have fundamentals older than %d days or missing entirely",
            len(stale_symbols),
            STALE_FUNDAMENTALS_SYMBOL_DAYS,
        )

    return {
        "flagged": bool(stale_symbols),
        "stale_symbol_count": len(stale_symbols),
        "stale_symbols": stale_symbols,
    }


def _check_fundamentals_table_freshness(latest_date) -> dict[str, Any]:
    with get_session() as session:
        most_recent = session.execute(text("SELECT max(reported_date) FROM fundamentals")).scalar()

    if most_recent is None:
        flagged = True
        days_stale = None
    else:
        days_stale = (latest_date - most_recent).days
        flagged = days_stale > STALE_FUNDAMENTALS_TABLE_DAYS

    if flagged:
        logger.warning(
            "data_quality: fundamentals table has not been refreshed in over %d days (most recent reported_date=%s) "
            "- likely a systemic backfill failure, not just one stale symbol",
            STALE_FUNDAMENTALS_TABLE_DAYS,
            most_recent,
        )

    return {"flagged": flagged, "most_recent_reported_date": most_recent, "days_stale": days_stale}


def _check_sector_fundamental_baseline_freshness(latest_date) -> dict[str, Any]:
    with get_session() as session:
        most_recent = session.execute(text("SELECT max(computed_at) FROM sector_fundamental_baseline")).scalar()

    if most_recent is None:
        flagged = True
        days_stale = None
    else:
        days_stale = (latest_date - most_recent.date()).days
        flagged = days_stale > STALE_SECTOR_FUNDAMENTAL_BASELINE_DAYS

    if flagged:
        logger.warning(
            "data_quality: sector_fundamental_baseline has not been refreshed in over %d days (most recent computed_at=%s) "
            "- likely a systemic automation failure",
            STALE_SECTOR_FUNDAMENTAL_BASELINE_DAYS,
            most_recent,
        )

    return {"flagged": flagged, "most_recent_computed_at": most_recent, "days_stale": days_stale}


def _check_signal_call_extraction_liveness(latest_date) -> dict[str, Any]:
    if not is_market_open_today():
        return {"flagged": False, "skipped": "not a trading day"}

    recent_days = _trailing_trading_days(
        latest_date + timedelta(days=1), SIGNAL_CALL_EXTRACTION_STALL_CONSECUTIVE_DAYS
    )
    if len(recent_days) < SIGNAL_CALL_EXTRACTION_STALL_CONSECUTIVE_DAYS:
        logger.warning(
            "data_quality: fewer than %d trading days on record, skipping signal_calls liveness check",
            SIGNAL_CALL_EXTRACTION_STALL_CONSECUTIVE_DAYS,
        )
        return {"flagged": False}

    with get_session() as session:
        rows_created = session.execute(
            text(
                "SELECT count(*) FROM signal_calls WHERE created_at::date >= :start AND created_at::date <= :end"
            ),
            {"start": min(recent_days), "end": max(recent_days)},
        ).scalar()

    flagged = rows_created == 0
    if flagged:
        logger.warning(
            "data_quality: zero signal_calls rows created across all 4 target signals in the last %d "
            "trading days (%s to %s) - extract_signal_calls.py may have stopped running, not just that "
            "no signals fired",
            SIGNAL_CALL_EXTRACTION_STALL_CONSECUTIVE_DAYS,
            min(recent_days),
            max(recent_days),
        )

    return {"flagged": flagged, "checked_days": recent_days, "rows_created": rows_created}


def check_daily_pipeline_health() -> dict[str, Any]:
    latest_date = _latest_price_date()
    if latest_date is None:
        logger.warning("data_quality: no rows in daily_prices, skipping health check")
        return {"latest_date": None, "checks_run": 0, "checks_flagged": 0}

    logger.info("data_quality: running health checks for %s", latest_date)

    results = {}
    for name, check in (
        ("row_count_anomaly", _check_row_count_anomaly),
        ("missing_symbols", _check_missing_symbols),
        ("price_sanity", _check_price_sanity),
        ("null_or_zero_prices", _check_null_or_zero_prices),
        ("fundamentals_symbol_staleness", _check_fundamentals_symbol_staleness),
        ("fundamentals_table_freshness", _check_fundamentals_table_freshness),
        ("sector_fundamental_baseline_freshness", _check_sector_fundamental_baseline_freshness),
        ("signal_call_extraction_liveness", _check_signal_call_extraction_liveness),
    ):
        try:
            results[name] = check(latest_date)
        except Exception:
            logger.exception("data_quality: %s check failed to run", name)
            results[name] = {"flagged": False, "error": True}

    checks_flagged = sum(1 for result in results.values() if result.get("flagged"))

    summary = {
        "latest_date": latest_date,
        "checks_run": len(results),
        "checks_flagged": checks_flagged,
        "results": results,
    }

    logger.info(
        "data_quality: health check complete for %s - %d/%d checks flagged",
        latest_date,
        checks_flagged,
        len(results),
    )

    return summary
