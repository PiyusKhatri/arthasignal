from __future__ import annotations

import logging
from datetime import date
from typing import Any

from src.pipeline.backfill_calendar import is_market_open_today
from src.pipeline.db_writers import upsert_recent_market_index_rows
from src.scrapers import nepse_api, sharesansar_scraper

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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
                    "close": row.get("close"),
                    "points_change": row.get("pointChange"),
                    "percent_change": row.get("percentChange"),
                }
            )
    except Exception:
        logger.exception("Failed to fetch sub-indices from sharesansar_scraper")

    return raw_rows


def run_daily_index_refresh(today: date | None = None) -> dict[str, Any]:
    if today is None:
        if not is_market_open_today():
            logger.info("Not a trading day, skipping daily index refresh")
            return {"skipped": True, "reason": "not a trading day"}
        today = date.today()

    raw_rows = _fetch_raw_index_rows()
    logger.info("Fetched %d index rows for %s", len(raw_rows), today)

    indices_processed = 0
    rows_upserted = 0
    failures = 0

    for raw_row in raw_rows:
        indices_processed += 1
        try:
            close = raw_row["close"]
            db_row = {
                "index_name": raw_row["index_name"],
                "date": today,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "points_change": raw_row["points_change"],
                "percent_change": raw_row["percent_change"],
            }
            inserted, _skipped = upsert_recent_market_index_rows([db_row])
            rows_upserted += inserted
        except Exception:
            logger.exception("Failed to upsert index %s", raw_row.get("index_name"))
            failures += 1

    summary = {
        "skipped": False,
        "date": today,
        "indices_fetched": len(raw_rows),
        "indices_processed": indices_processed,
        "rows_upserted": rows_upserted,
        "failures": failures,
    }
    logger.info("Daily index refresh summary: %s", summary)
    return summary


if __name__ == "__main__":
    run_daily_index_refresh()
