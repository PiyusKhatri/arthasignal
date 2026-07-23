from __future__ import annotations

import logging
import time
from typing import Any

from src.pipeline.db_writers import insert_new_symbol_history
from src.scrapers.symbol_history_scraper import get_symbol_history

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_symbol_history_backfill() -> dict[str, Any]:
    start_time = time.perf_counter()

    records = get_symbol_history()
    logger.info("Resolved %d symbol history records to insert", len(records))

    rows_inserted, duplicates_skipped = insert_new_symbol_history(records)

    elapsed_seconds = time.perf_counter() - start_time

    summary = {
        "records_resolved": len(records),
        "rows_inserted": rows_inserted,
        "duplicates_skipped": duplicates_skipped,
        "execution_time_seconds": round(elapsed_seconds, 2),
    }

    logger.info(
        "Symbol history backfill summary: records_resolved=%d rows_inserted=%d duplicates_skipped=%d "
        "execution_time_seconds=%.2f",
        summary["records_resolved"],
        summary["rows_inserted"],
        summary["duplicates_skipped"],
        summary["execution_time_seconds"],
    )

    return summary


if __name__ == "__main__":
    run_symbol_history_backfill()
