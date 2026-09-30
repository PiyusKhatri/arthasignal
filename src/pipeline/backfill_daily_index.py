from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from src.pipeline.backfill_calendar import is_trading_day
from src.pipeline.db_writers import upsert_recent_market_index_rows
from src.scrapers import nepse_api, sharesansar_scraper

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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

    summary = {
        "skipped": False,
        "run_date": run_date,
        "session_dates": sorted(session_dates),
        "indices_fetched": len(raw_rows),
        "indices_processed": indices_processed,
        "rows_upserted": rows_upserted,
        "rows_without_source_session_date": missing_session_date,
        "failures": failures,
    }
    logger.info("Daily index refresh summary: %s", summary)
    return summary


if __name__ == "__main__":
    run_daily_index_refresh()
