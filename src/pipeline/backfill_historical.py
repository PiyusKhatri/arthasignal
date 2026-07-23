from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select

from src.database.connection import get_session
from src.database.models import DailyPrice
from src.pipeline.db_writers import build_company_records, insert_new_daily_prices, upsert_companies
from src.scrapers import sharesansar_scraper
from src.scrapers.symbols import get_all_listed_symbols

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BACKFILL_YEARS = 5
RESUME_TOLERANCE_DAYS = 10
PROGRESS_LOG_INTERVAL = 20


def _earliest_stored_date(symbol: str) -> date | None:
    with get_session() as session:
        return session.execute(select(func.min(DailyPrice.date)).where(DailyPrice.symbol == symbol)).scalar()


def _symbol_already_backfilled(symbol: str, cutoff_date: date) -> bool:
    earliest = _earliest_stored_date(symbol)
    if earliest is None:
        return False
    return earliest <= cutoff_date + timedelta(days=RESUME_TOLERANCE_DAYS)


def run_backfill(symbols: list[str] | None = None, years: int = BACKFILL_YEARS) -> dict[str, Any]:
    start_time = time.perf_counter()

    if symbols is None:
        symbols = get_all_listed_symbols()
    logger.info("Backfilling %d symbols for up to %d years of history", len(symbols), years)

    company_records = build_company_records(symbols)
    try:
        upsert_companies(company_records)
    except Exception:
        logger.exception("Failed to upsert companies before backfill")

    cutoff_date = date.today() - timedelta(days=years * 365)

    symbols_processed = 0
    symbols_skipped = 0
    rows_inserted_total = 0
    duplicates_skipped_total = 0
    failures = 0

    for symbol in symbols:
        symbols_processed += 1
        try:
            if _symbol_already_backfilled(symbol, cutoff_date):
                symbols_skipped += 1
                logger.info("%s: already has full history, skipping", symbol)
                continue

            history_rows = sharesansar_scraper.get_price_history(symbol, years=years)
            if not history_rows:
                logger.warning("%s: no historical rows returned", symbol)
                continue

            inserted, skipped = insert_new_daily_prices(history_rows)
            rows_inserted_total += inserted
            duplicates_skipped_total += skipped
        except Exception:
            logger.exception("Failed to backfill symbol %s", symbol)
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
        "Backfill summary: symbols_processed=%d symbols_skipped=%d rows_inserted=%d "
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
    run_backfill()
