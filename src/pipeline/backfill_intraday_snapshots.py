from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import Company, IntradaySnapshot
from src.pipeline.market_hours_guard import _current_npt_time, is_within_intraday_window
from src.scrapers.intraday_price_scraper import get_intraday_snapshot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SNAPSHOT_ROUND_MINUTES = 15


def _round_to_nearest_15_minutes(dt: datetime) -> datetime:
    dt = dt.replace(second=0, microsecond=0)
    remainder = dt.minute % SNAPSHOT_ROUND_MINUTES
    if remainder < SNAPSHOT_ROUND_MINUTES / 2:
        return dt - timedelta(minutes=remainder)
    return dt + timedelta(minutes=SNAPSHOT_ROUND_MINUTES - remainder)


def _known_symbols() -> set[str]:
    with get_session() as session:
        rows = session.execute(select(Company.symbol)).all()
        return {r.symbol for r in rows}


def _insert_snapshots(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(IntradaySnapshot).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=["symbol", "snapshot_time"])
    stmt = stmt.returning(IntradaySnapshot.id)
    with get_session() as session:
        result = session.execute(stmt)
        return len(result.fetchall())


def run_intraday_snapshot_backfill() -> dict[str, Any]:
    if not is_within_intraday_window():
        logger.info("Outside NEPSE intraday trading window, skipping intraday snapshot backfill")
        return {"skipped": True, "reason": "outside intraday trading window"}

    snapshot_time = _round_to_nearest_15_minutes(_current_npt_time())
    logger.info("Fetching intraday snapshot for snapshot_time=%s", snapshot_time)

    snapshot_rows = get_intraday_snapshot()
    known_symbols = _known_symbols()

    rows = []
    skipped_unknown_symbol = 0
    for row in snapshot_rows:
        if row["symbol"] not in known_symbols:
            skipped_unknown_symbol += 1
            continue
        rows.append({**row, "snapshot_time": snapshot_time})

    inserted = _insert_snapshots(rows)
    duplicates_skipped = len(rows) - inserted

    summary = {
        "skipped": False,
        "snapshot_time": snapshot_time,
        "symbols_fetched": len(snapshot_rows),
        "skipped_unknown_symbol": skipped_unknown_symbol,
        "rows_inserted": inserted,
        "duplicates_skipped": duplicates_skipped,
    }
    logger.info("Intraday snapshot backfill summary: %s", summary)
    return summary


if __name__ == "__main__":
    run_intraday_snapshot_backfill()
