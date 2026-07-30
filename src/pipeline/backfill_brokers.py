from __future__ import annotations

import logging
from typing import Any

from src.pipeline.db_writers import upsert_brokers
from src.scrapers import nepse_api

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_broker_backfill() -> dict[str, Any]:
    raw_brokers = nepse_api.get_brokers()
    logger.info("Fetched %d brokers from nepse-scraper", len(raw_brokers))

    records = []
    for row in raw_brokers:
        member_code = row.get("memberCode")
        member_name = row.get("memberName")
        if member_code is None or not member_name:
            continue
        records.append(
            {
                "broker_id": str(member_code),
                "broker_name": member_name,
                "is_active": row.get("activeStatus") == "A",
            }
        )

    upserted = upsert_brokers(records)

    summary = {"brokers_fetched": len(raw_brokers), "brokers_upserted": upserted}
    logger.info("Broker backfill summary: %s", summary)
    return summary


if __name__ == "__main__":
    run_broker_backfill()
