from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import MarketIndex
from src.pipeline.backfill_daily_index import TRACKED_INDEX_NAMES
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


def insert_index_rows_only_new(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    stmt = (
        pg_insert(MarketIndex)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["index_name", "date"])
        .returning(MarketIndex.id)
    )
    with get_session() as session:
        return len(session.execute(stmt).fetchall())


def run_index_range_backfill(start: date, end: date, index_names: list[str] | None = None) -> dict[str, Any]:
    index_names = index_names or TRACKED_INDEX_NAMES
    summary: dict[str, Any] = {"start": start.isoformat(), "end": end.isoformat(), "indices": {}}
    for index_name in index_names:
        try:
            rows = [
                row
                for row in get_index_history(index_name, start_date=start, end_date=end)
                if row.get("close") is not None and start <= row["date"] <= end
            ]
            for row in rows:
                for column in ("open", "high", "low"):
                    if row.get(column) is None:
                        row[column] = row["close"]
                for column in ("points_change", "percent_change"):
                    if row.get(column) is None:
                        row[column] = 0
            unique = list({row["date"]: row for row in rows}.values())
            inserted = 0
            for offset in range(0, len(unique), 1000):
                inserted += insert_index_rows_only_new(unique[offset : offset + 1000])
            summary["indices"][index_name] = {"source_rows": len(unique), "inserted": inserted}
        except Exception as exc:
            logger.exception("Failed to backfill index %s", index_name)
            summary["indices"][index_name] = {"error": str(exc)}
        logger.info("%s: %s", index_name, summary["indices"][index_name])
    return summary


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Backfill index history")
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    args = parser.parse_args()
    if args.start and args.end:
        print(json.dumps(run_index_range_backfill(args.start, args.end), indent=2))
    else:
        run_index_backfill()
