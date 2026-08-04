from __future__ import annotations

import logging
from typing import Any

from src.notifications.discord_alert import send_discord_alert
from src.pipeline.backfill_intraday_index_snapshots import run_intraday_index_snapshot_backfill
from src.pipeline.backfill_intraday_snapshots import run_intraday_snapshot_backfill
from src.pipeline.intraday_data_quality import check_intraday_data_quality

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

    try:
        quality_summary = check_intraday_data_quality(price_summary, index_summary)
    except Exception:
        logger.exception("check_intraday_data_quality failed")
        quality_summary = {"run_checks": {}, "daily_coverage": {}, "should_alert": False}

    if quality_summary["should_alert"]:
        coverage = quality_summary["daily_coverage"]
        zero_coverage = any(v["realized_snapshots"] == 0 for v in coverage.values())
        lines = [
            f"{v['table']}: {v['realized_snapshots']}/{v['expected_snapshots_so_far']} snapshots today "
            f"({(v['coverage_ratio'] or 0) * 100:.0f}%)"
            for v in coverage.values()
        ]
        if zero_coverage:
            send_discord_alert(
                "ZERO INTRADAY SNAPSHOTS - possible silent pipeline failure, not just low coverage:\n"
                + "\n".join(lines),
                severity="failure",
            )
        else:
            send_discord_alert("Intraday snapshot coverage is low today:\n" + "\n".join(lines), severity="warning")

    return {"price_summary": price_summary, "index_summary": index_summary, "quality_summary": quality_summary}


if __name__ == "__main__":
    run_intraday_pipeline()
