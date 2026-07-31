from __future__ import annotations

import logging
import time
from typing import Any

from src.pipeline.db_writers import upsert_ipo_calendar
from src.scrapers.ipo_calendar_scraper import get_ipo_calendar

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_ipo_calendar_backfill() -> dict[str, Any]:
    start_time = time.perf_counter()

    try:
        rows = get_ipo_calendar()
    except Exception:
        logger.exception("Failed to fetch IPO calendar data")
        return {"rows_fetched": 0, "rows_upserted": 0, "failures": 1}

    upserted, failed_batches = upsert_ipo_calendar(rows)
    elapsed_seconds = time.perf_counter() - start_time

    summary = {
        "rows_fetched": len(rows),
        "rows_upserted": upserted,
        "failures": failed_batches,
        "execution_time_seconds": round(elapsed_seconds, 2),
    }
    logger.info("IPO calendar backfill summary: %s", summary)
    return summary


if __name__ == "__main__":
    run_ipo_calendar_backfill()
