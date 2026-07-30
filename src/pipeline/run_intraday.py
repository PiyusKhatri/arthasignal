from __future__ import annotations

import logging
from typing import Any

from src.notifications.discord_alert import send_discord_alert
from src.pipeline.backfill_intraday_index_snapshots import run_intraday_index_snapshot_backfill
from src.pipeline.backfill_intraday_snapshots import run_intraday_snapshot_backfill

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_intraday_pipeline() -> dict[str, Any]:
    try:
        price_summary = run_intraday_snapshot_backfill()
    except Exception as exc:
        logger.exception("run_intraday_snapshot_backfill failed")
        send_discord_alert(f"Intraday price snapshot failed: {exc}", severity="failure")
        raise

    try:
        index_summary = run_intraday_index_snapshot_backfill()
    except Exception as exc:
        logger.exception("run_intraday_index_snapshot_backfill failed")
        send_discord_alert(f"Intraday index snapshot failed: {exc}", severity="failure")
        raise

    logger.info("Intraday pipeline summary: price=%s index=%s", price_summary, index_summary)

    return {"price_summary": price_summary, "index_summary": index_summary}


if __name__ == "__main__":
    run_intraday_pipeline()
