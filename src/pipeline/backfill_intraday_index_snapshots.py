from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import IntradayIndexSnapshot
from src.pipeline.backfill_intraday_snapshots import _round_to_nearest_15_minutes
from src.pipeline.market_hours_guard import _current_npt_time, is_within_intraday_window
from src.scrapers.intraday_index_scraper import get_intraday_index_snapshot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _insert_snapshots(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(IntradayIndexSnapshot).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=["index_name", "snapshot_time"])
    stmt = stmt.returning(IntradayIndexSnapshot.id)
    with get_session() as session:
        result = session.execute(stmt)
        return len(result.fetchall())


def run_intraday_index_snapshot_backfill() -> dict[str, Any]:
    if not is_within_intraday_window():
        logger.info("Outside NEPSE intraday trading window, skipping intraday index snapshot backfill")
        return {"skipped": True, "reason": "outside intraday trading window"}

    snapshot_time = _round_to_nearest_15_minutes(_current_npt_time())
    logger.info("Fetching intraday index snapshot for snapshot_time=%s", snapshot_time)

    snapshot_rows = get_intraday_index_snapshot()
    rows = [{**row, "snapshot_time": snapshot_time} for row in snapshot_rows]

    inserted = _insert_snapshots(rows)
    duplicates_skipped = len(rows) - inserted

    summary = {
        "skipped": False,
        "snapshot_time": snapshot_time,
        "indices_fetched": len(snapshot_rows),
        "rows_inserted": inserted,
        "duplicates_skipped": duplicates_skipped,
    }
    logger.info("Intraday index snapshot backfill summary: %s", summary)
    return summary


if __name__ == "__main__":
    run_intraday_index_snapshot_backfill()
