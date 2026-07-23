from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import select

from src.database.connection import get_session
from src.database.models import CorporateAction
from src.pipeline.db_writers import build_company_records, insert_new_corporate_actions, upsert_companies
from src.scrapers.corporate_actions_scraper import get_corporate_actions
from src.scrapers.symbols import get_all_listed_symbols

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROGRESS_LOG_INTERVAL = 20


def _symbol_already_backfilled(symbol: str) -> bool:
    with get_session() as session:
        row = session.execute(
            select(CorporateAction.id).where(CorporateAction.symbol == symbol).limit(1)
        ).first()
    return row is not None


def run_corporate_actions_backfill(symbols: list[str] | None = None) -> dict[str, Any]:
    start_time = time.perf_counter()

    if symbols is None:
        symbols = get_all_listed_symbols()
    logger.info("Backfilling corporate actions for %d symbols", len(symbols))

    company_records = build_company_records(symbols)
    try:
        upsert_companies(company_records)
    except Exception:
        logger.exception("Failed to upsert companies before corporate actions backfill")

    symbols_processed = 0
    symbols_skipped = 0
    rows_inserted_total = 0
    duplicates_skipped_total = 0
    failures = 0

    for symbol in symbols:
        symbols_processed += 1
        try:
            if _symbol_already_backfilled(symbol):
                symbols_skipped += 1
                logger.info("%s: already has corporate action records, skipping", symbol)
                continue

            actions = get_corporate_actions(symbol)
            if not actions:
                logger.info("%s: no corporate action records found", symbol)
                continue

            inserted, skipped = insert_new_corporate_actions(actions)
            rows_inserted_total += inserted
            duplicates_skipped_total += skipped
        except Exception:
            logger.exception("Failed to backfill corporate actions for symbol %s", symbol)
            failures += 1

        if symbols_processed % PROGRESS_LOG_INTERVAL == 0:
            logger.info(
                "Progress: %d/%d symbols done, rows_inserted=%d duplicates_skipped=%d failures=%d",
                symbols_processed,
                len(symbols),
                rows_inserted_total,
                duplicates_skipped_total,
                failures,
            )

    elapsed_seconds = time.perf_counter() - start_time

    summary = {
        "symbols_processed": symbols_processed,
        "symbols_skipped_already_backfilled": symbols_skipped,
        "rows_inserted": rows_inserted_total,
        "duplicates_skipped": duplicates_skipped_total,
        "failures": failures,
        "execution_time_seconds": round(elapsed_seconds, 2),
    }

    logger.info(
        "Corporate actions backfill summary: symbols_processed=%d symbols_skipped=%d rows_inserted=%d "
        "duplicates_skipped=%d failures=%d execution_time_seconds=%.2f",
        summary["symbols_processed"],
        summary["symbols_skipped_already_backfilled"],
        summary["rows_inserted"],
        summary["duplicates_skipped"],
        summary["failures"],
        summary["execution_time_seconds"],
    )

    return summary


if __name__ == "__main__":
    run_corporate_actions_backfill()
