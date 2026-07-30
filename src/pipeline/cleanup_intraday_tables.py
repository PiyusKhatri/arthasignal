from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete

from src.database.connection import get_session
from src.database.models import IntradayFloorsheet, IntradayIndexSnapshot, IntradaySnapshot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

RETENTION_DAYS = 30

RETENTION_TABLES = {
    "intraday_snapshots": IntradaySnapshot,
    "intraday_index_snapshots": IntradayIndexSnapshot,
    "intraday_floorsheet": IntradayFloorsheet,
}


def _delete_old_rows(model: Any, cutoff: datetime) -> int:
    stmt = delete(model).where(model.snapshot_time < cutoff).returning(model.id)
    with get_session() as session:
        result = session.execute(stmt)
        return len(result.fetchall())


def run_intraday_table_cleanup(retention_days: int = RETENTION_DAYS) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    logger.info("Deleting intraday rows older than %s (retention_days=%d)", cutoff, retention_days)

    deleted_by_table: dict[str, int] = {}
    failures = 0
    for table_name, model in RETENTION_TABLES.items():
        try:
            deleted = _delete_old_rows(model, cutoff)
            deleted_by_table[table_name] = deleted
            logger.info("%s: deleted %d rows older than %s", table_name, deleted, cutoff)
        except Exception:
            logger.exception("Failed to clean up %s", table_name)
            deleted_by_table[table_name] = 0
            failures += 1

    summary = {
        "retention_days": retention_days,
        "cutoff": cutoff,
        "deleted_by_table": deleted_by_table,
        "total_deleted": sum(deleted_by_table.values()),
        "failures": failures,
    }
    logger.info("Intraday table cleanup summary: %s", summary)
    return summary


if __name__ == "__main__":
    run_intraday_table_cleanup()
