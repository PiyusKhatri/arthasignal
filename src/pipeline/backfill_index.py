from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select

from src.database.connection import get_session
from src.database.models import MarketIndex
from src.pipeline.db_writers import insert_new_market_index_rows
from src.scrapers.index_scraper import INDEX_NAME_TO_ID, get_index_history

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BACKFILL_YEARS = 5
RESUME_TOLERANCE_DAYS = 10
PROGRESS_LOG_INTERVAL = 5


def _earliest_stored_date(index_name: str) -> date | None:
    with get_session() as session:
        return session.execute(
            select(func.min(MarketIndex.date)).where(MarketIndex.index_name == index_name)
        ).scalar()


def _index_already_backfilled(index_name: str, cutoff_date: date) -> bool:
    earliest = _earliest_stored_date(index_name)
    if earliest is None:
        return False
    return earliest <= cutoff_date + timedelta(days=RESUME_TOLERANCE_DAYS)


def run_index_backfill(index_names: list[str] | None = None, years: int = BACKFILL_YEARS) -> dict[str, Any]:
    start_time = time.perf_counter()

    if index_names is None:
        index_names = list(INDEX_NAME_TO_ID.keys())
    logger.info("Backfilling %d indices for up to %d years of history", len(index_names), years)

    cutoff_date = date.today() - timedelta(days=years * 365)

    indices_processed = 0
    indices_skipped = 0
    rows_inserted_total = 0
    duplicates_skipped_total = 0
    failures = 0

    for index_name in index_names:
        indices_processed += 1
        try:
            if _index_already_backfilled(index_name, cutoff_date):
                indices_skipped += 1
                logger.info("%s: already has full history, skipping", index_name)
                continue

            history_rows = get_index_history(index_name, years=years)
            if not history_rows:
                logger.warning("%s: no historical rows returned", index_name)
                continue

            inserted, skipped = insert_new_market_index_rows(history_rows)
            rows_inserted_total += inserted
            duplicates_skipped_total += skipped
        except Exception:
            logger.exception("Failed to backfill index %s", index_name)
            failures += 1

        if indices_processed % PROGRESS_LOG_INTERVAL == 0:
            logger.info(
                "Progress: %d/%d indices done, rows_inserted=%d duplicates_skipped=%d failures=%d",
                indices_processed,
                len(index_names),
                rows_inserted_total,
                duplicates_skipped_total,
                failures,
            )

    elapsed_seconds = time.perf_counter() - start_time

    summary = {
        "indices_processed": indices_processed,
        "indices_skipped_already_backfilled": indices_skipped,
        "rows_inserted": rows_inserted_total,
        "duplicates_skipped": duplicates_skipped_total,
        "failures": failures,
        "execution_time_seconds": round(elapsed_seconds, 2),
    }

    logger.info(
        "Index backfill summary: indices_processed=%d indices_skipped=%d rows_inserted=%d "
        "duplicates_skipped=%d failures=%d execution_time_seconds=%.2f",
        summary["indices_processed"],
        summary["indices_skipped_already_backfilled"],
        summary["rows_inserted"],
        summary["duplicates_skipped"],
        summary["failures"],
        summary["execution_time_seconds"],
    )

    return summary


if __name__ == "__main__":
    run_index_backfill()
