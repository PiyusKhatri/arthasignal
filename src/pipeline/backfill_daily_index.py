from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy import select

from src.database.connection import get_session
from src.database.models import DailyPrice, MarketIndex
from src.pipeline.backfill_calendar import is_trading_day
from src.pipeline.db_writers import upsert_recent_market_index_rows
from src.scrapers import nepse_api, sharesansar_scraper
from src.scrapers.index_scraper import INDEX_NAME_TO_ID, get_index_history

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FALLBACK_LOOKBACK_SESSIONS = 10
DISCONTINUED_INDEX_NAMES = {"Insurance"}
TRACKED_INDEX_NAMES = sorted(set(INDEX_NAME_TO_ID) - DISCONTINUED_INDEX_NAMES)


def parse_session_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _fetch_raw_index_rows() -> list[dict[str, Any]]:
    raw_rows: list[dict[str, Any]] = []

    try:
        for row in nepse_api.get_nepse_index():
            index_name = row.get("index")
            if not index_name:
                continue
            raw_rows.append(
                {
                    "index_name": index_name,
                    "session_date": parse_session_date(row.get("generatedTime")),
                    "open": None,
                    "high": row.get("high"),
                    "low": row.get("low"),
                    "close": row.get("currentValue"),
                    "points_change": row.get("change"),
                    "percent_change": row.get("perChange"),
                }
            )
    except Exception:
        logger.exception("Failed to fetch broad indices from nepse_api")

    try:
        for row in sharesansar_scraper.scrape_sub_indices():
            index_name = row.get("indexName")
            if not index_name:
                continue
            raw_rows.append(
                {
                    "index_name": index_name,
                    "session_date": parse_session_date(row.get("asOfDate")),
                    "open": row.get("open"),
                    "high": row.get("high"),
                    "low": row.get("low"),
                    "close": row.get("close"),
                    "points_change": row.get("pointChange"),
                    "percent_change": row.get("percentChange"),
                }
            )
    except Exception:
        logger.exception("Failed to fetch sub-indices from sharesansar_scraper")

    return raw_rows


def build_index_row(raw_row: dict[str, Any]) -> dict[str, Any] | None:
    session_date = raw_row.get("session_date")
    close = raw_row.get("close")
    if session_date is None or close is None:
        return None
    return {
        "index_name": raw_row["index_name"],
        "date": session_date,
        "open": raw_row.get("open") if raw_row.get("open") is not None else close,
        "high": raw_row.get("high") if raw_row.get("high") is not None else close,
        "low": raw_row.get("low") if raw_row.get("low") is not None else close,
        "close": close,
        "points_change": raw_row["points_change"],
        "percent_change": raw_row["percent_change"],
    }


def _recent_price_sessions(count: int) -> list[date]:
    with get_session() as session:
        rows = session.execute(
            select(DailyPrice.date).distinct().order_by(DailyPrice.date.desc()).limit(count)
        ).scalars().all()
    return sorted(rows)


def _stored_index_keys(start_date: date) -> set[tuple[str, date]]:
    with get_session() as session:
        rows = session.execute(
            select(MarketIndex.index_name, MarketIndex.date).where(MarketIndex.date >= start_date)
        ).all()
    return {(row.index_name, row.date) for row in rows}


def find_missing_index_sessions(
    sessions: list[date],
    present: set[tuple[str, date]],
    index_names: list[str],
) -> dict[str, list[date]]:
    missing: dict[str, list[date]] = {}
    for index_name in index_names:
        dates = [day for day in sessions if (index_name, day) not in present]
        if dates:
            missing[index_name] = dates
    return missing


def _fetch_fallback_rows(missing: dict[str, list[date]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index_name, dates in missing.items():
        try:
            history = get_index_history(index_name, start_date=min(dates), end_date=max(dates))
        except Exception:
            logger.exception("Fallback index history fetch failed for %s", index_name)
            continue
        by_date = {row["date"]: row for row in history if row.get("close") is not None}
        rows.extend(by_date[day] for day in dates if day in by_date)
    return rows


def fill_missing_index_rows(fetched_rows: list[dict[str, Any]]) -> dict[str, Any]:
    sessions = _recent_price_sessions(FALLBACK_LOOKBACK_SESSIONS)
    if not sessions:
        return {"sessions_checked": 0, "fallback_rows_upserted": 0, "still_missing": {}}
    present = _stored_index_keys(sessions[0]) | {(row["index_name"], row["date"]) for row in fetched_rows}
    missing = find_missing_index_sessions(sessions, present, TRACKED_INDEX_NAMES)
    fallback_rows = _fetch_fallback_rows(missing) if missing else []
    upserted = 0
    for row in fallback_rows:
        inserted, _ = upsert_recent_market_index_rows([row])
        upserted += inserted
        present.add((row["index_name"], row["date"]))
    still_missing = find_missing_index_sessions(sessions, present, TRACKED_INDEX_NAMES)
    if missing:
        logger.warning("Index rows missing after primary refresh, filled from index history: %s", missing)
    if still_missing:
        logger.error("INDEX ROWS STILL MISSING after fallback: %s", still_missing)
    return {
        "sessions_checked": len(sessions),
        "missing_before_fallback": {name: [d.isoformat() for d in days] for name, days in missing.items()},
        "fallback_rows_upserted": upserted,
        "still_missing": {name: [d.isoformat() for d in days] for name, days in still_missing.items()},
    }


def run_daily_index_refresh(today: date | None = None) -> dict[str, Any]:
    if today is None and not is_trading_day():
        logger.info("Not a trading day, skipping daily index refresh")
        return {"skipped": True, "reason": "not a trading day"}
    run_date = today or date.today()

    raw_rows = _fetch_raw_index_rows()
    logger.info("Fetched %d index rows on run date %s", len(raw_rows), run_date)

    indices_processed = 0
    rows_upserted = 0
    failures = 0
    missing_session_date = 0
    session_dates: set[date] = set()

    for raw_row in raw_rows:
        indices_processed += 1
        try:
            db_row = build_index_row(raw_row)
            if db_row is None:
                logger.error(
                    "Index %s has no source session date or close; refusing to stamp it with the run date",
                    raw_row.get("index_name"),
                )
                missing_session_date += 1
                failures += 1
                continue
            inserted, _skipped = upsert_recent_market_index_rows([db_row])
            rows_upserted += inserted
            session_dates.add(db_row["date"])
        except Exception:
            logger.exception("Failed to upsert index %s", raw_row.get("index_name"))
            failures += 1

    stored_rows = [
        {"index_name": raw_row["index_name"], "date": raw_row["session_date"]}
        for raw_row in raw_rows
        if raw_row.get("session_date") is not None and raw_row.get("close") is not None
    ]
    try:
        fallback = fill_missing_index_rows(stored_rows)
    except Exception:
        logger.exception("Index fallback failed")
        fallback = {"error": True}
        failures += 1
    if fallback.get("still_missing"):
        failures += sum(len(days) for days in fallback["still_missing"].values())

    summary = {
        "skipped": False,
        "run_date": run_date,
        "session_dates": sorted(session_dates),
        "indices_fetched": len(raw_rows),
        "indices_processed": indices_processed,
        "rows_upserted": rows_upserted,
        "rows_without_source_session_date": missing_session_date,
        "fallback": fallback,
        "failures": failures,
    }
    logger.info("Daily index refresh summary: %s", summary)
    return summary


if __name__ == "__main__":
    run_daily_index_refresh()
