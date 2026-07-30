from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any

from sqlalchemy import select

from src.database.connection import get_session
from src.database.models import Company
from src.pipeline.db_writers import insert_new_promoter_holding
from src.scrapers.promoter_holding_scraper import get_promoter_holding

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _known_symbols() -> set[str]:
    with get_session() as session:
        rows = session.execute(select(Company.symbol)).all()
        return {r.symbol for r in rows}


def run_promoter_holding_backfill() -> dict[str, Any]:
    start_time = time.perf_counter()

    try:
        raw_rows = get_promoter_holding()
    except Exception:
        logger.exception("Failed to fetch promoter holding data")
        return {"rows_fetched": 0, "rows_inserted": 0, "duplicates_skipped": 0, "skipped_unknown_symbol": 0, "failures": 1}

    logger.info("Fetched %d promoter holding rows", len(raw_rows))

    known_symbols = _known_symbols()
    today = date.today()

    rows_inserted_total = 0
    duplicates_skipped_total = 0
    skipped_unknown_symbol = 0
    failures = 0

    for row in raw_rows:
        try:
            if row["symbol"] not in known_symbols:
                skipped_unknown_symbol += 1
                continue

            db_row = {**row, "reported_date": row["reported_date"] or today}
            inserted, skipped = insert_new_promoter_holding([db_row])
            rows_inserted_total += inserted
            duplicates_skipped_total += skipped
        except Exception:
            logger.exception("Failed to backfill promoter holding for %s", row.get("symbol"))
            failures += 1

    elapsed_seconds = time.perf_counter() - start_time

    summary = {
        "rows_fetched": len(raw_rows),
        "rows_inserted": rows_inserted_total,
        "duplicates_skipped": duplicates_skipped_total,
        "skipped_unknown_symbol": skipped_unknown_symbol,
        "failures": failures,
        "execution_time_seconds": round(elapsed_seconds, 2),
    }

    logger.info(
        "Promoter holding backfill summary: rows_fetched=%d rows_inserted=%d duplicates_skipped=%d "
        "skipped_unknown_symbol=%d failures=%d execution_time_seconds=%.2f",
        summary["rows_fetched"],
        summary["rows_inserted"],
        summary["duplicates_skipped"],
        summary["skipped_unknown_symbol"],
        summary["failures"],
        summary["execution_time_seconds"],
    )

    return summary


if __name__ == "__main__":
    run_promoter_holding_backfill()
