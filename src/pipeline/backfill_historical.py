from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# ArthaSignal's historical analytics are designed around long-cycle NEPSE data.
# Keep the default aligned with the full-history backfill rather than silently
# falling back to the old five-year window when --years is omitted.
BACKFILL_YEARS = 20
RESUME_TOLERANCE_DAYS = 10
PROGRESS_LOG_INTERVAL = 20
# Max safe parallel workers for ShareSansar — more than 4 risks rate-limiting
MAX_WORKERS = 4


def _earliest_stored_date(symbol: str) -> date | None:
    with get_session() as session:
        return session.execute(
            select(func.min(DailyPrice.date)).where(DailyPrice.symbol == symbol)
        ).scalar()


def _symbol_already_backfilled(symbol: str, cutoff_date: date) -> bool:
    earliest = _earliest_stored_date(symbol)
    if earliest is None:
        return False
    return earliest <= cutoff_date + timedelta(days=RESUME_TOLERANCE_DAYS)


def _backfill_one(symbol: str, years: int, cutoff_date: date) -> dict[str, Any]:
    """Fetch and store history for a single symbol. Safe to call from any thread."""
    if _symbol_already_backfilled(symbol, cutoff_date):
        logger.info("%s: already has full history, skipping", symbol)
        return {"skipped": True, "inserted": 0, "duplicates": 0}

    history_rows = sharesansar_scraper.get_price_history(symbol, years=years)
    if not history_rows:
        logger.warning("%s: no historical rows returned", symbol)
        return {"skipped": False, "inserted": 0, "duplicates": 0}

    inserted, duplicates = insert_new_daily_prices(history_rows)
    return {"skipped": False, "inserted": inserted, "duplicates": duplicates}


def run_backfill(
    symbols: list[str] | None = None,
    years: int = BACKFILL_YEARS,
    workers: int = 1,
) -> dict[str, Any]:
    """
    Backfill price history for all (or a subset of) listed symbols.

    Args:
        symbols:  List of symbols to process. None = all listed symbols.
        years:    How many years of history to fetch.
        workers:  Number of parallel download threads (1 = sequential).
                  Recommended maximum: 4 (ShareSansar rate-limit safe).
    """
    start_time = time.perf_counter()
    workers = max(1, min(workers, MAX_WORKERS))

    if symbols is None:
        symbols = get_all_listed_symbols()
    logger.info(
        "Backfilling %d symbols for up to %d years of history using %d worker(s)",
        len(symbols),
        years,
        workers,
    )

    company_records = build_company_records(symbols)
    try:
        upsert_companies(company_records)
    except Exception:
        logger.exception("Failed to upsert companies before backfill")

    cutoff_date = date.today() - timedelta(days=years * 365)

    # Shared counters — guarded by a lock when workers > 1
    lock = threading.Lock()
    symbols_processed = 0
    symbols_skipped = 0
    rows_inserted_total = 0
    duplicates_skipped_total = 0
    failures = 0
    total = len(symbols)

    def _process(symbol: str) -> None:
        nonlocal symbols_processed, symbols_skipped, rows_inserted_total
        nonlocal duplicates_skipped_total, failures

        try:
            result = _backfill_one(symbol, years, cutoff_date)
        except Exception:
            logger.exception("Failed to backfill symbol %s", symbol)
            with lock:
                failures += 1
                symbols_processed += 1
            return

        with lock:
            symbols_processed += 1
            if result["skipped"]:
                symbols_skipped += 1
            else:
                rows_inserted_total += result["inserted"]
                duplicates_skipped_total += result["duplicates"]

            if symbols_processed % PROGRESS_LOG_INTERVAL == 0:
                logger.info(
                    "Progress: %d/%d symbols done, rows_inserted=%d duplicates_skipped=%d failures=%d",
                    symbols_processed,
                    total,
                    rows_inserted_total,
                    duplicates_skipped_total,
                    failures,
                )

    if workers == 1:
        for symbol in symbols:
            _process(symbol)
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="backfill") as pool:
            futures = {pool.submit(_process, sym): sym for sym in symbols}
            for future in as_completed(futures):
                exc = future.exception()
                if exc:
                    logger.error("Unhandled exception for %s: %s", futures[future], exc)

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
    import argparse

    parser = argparse.ArgumentParser(description="Backfill historical price data")
    parser.add_argument("--years", type=int, default=BACKFILL_YEARS, help="Years of history")
    parser.add_argument("--workers", type=int, default=1, help="Parallel workers (max 4)")
    args = parser.parse_args()
    run_backfill(years=args.years, workers=args.workers)
