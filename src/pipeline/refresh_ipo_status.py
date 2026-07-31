from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy import select, update

from src.database.connection import get_session
from src.database.models import IpoCalendar

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def refresh_ipo_status() -> dict[str, Any]:
    today = date.today()

    with get_session() as session:
        rows = session.execute(
            select(IpoCalendar.id, IpoCalendar.opening_date, IpoCalendar.closing_date, IpoCalendar.status)
        ).all()

        buckets: dict[str, list[int]] = {"upcoming": [], "open": [], "closed": []}

        for row in rows:
            if row.status == "allotted":
                continue

            if row.opening_date is None or row.closing_date is None or today < row.opening_date:
                target = "upcoming"
            elif today <= row.closing_date:
                target = "open"
            else:
                target = "closed"

            if target != row.status:
                buckets[target].append(row.id)

        updated = 0
        for target_status, ids in buckets.items():
            if not ids:
                continue
            session.execute(update(IpoCalendar).where(IpoCalendar.id.in_(ids)).values(status=target_status))
            updated += len(ids)

    summary = {"rows_checked": len(rows), "rows_updated": updated}
    logger.info("IPO status refresh summary: %s", summary)
    return summary


if __name__ == "__main__":
    refresh_ipo_status()
