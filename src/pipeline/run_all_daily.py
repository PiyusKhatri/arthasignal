from __future__ import annotations

import logging
import time
from typing import Any

from src.notifications.discord_alert import send_discord_alert
from src.pipeline.backfill_calendar import is_market_open_today
from src.pipeline.backup_to_drive import run_backup
from src.pipeline.data_quality import check_daily_pipeline_health
from src.pipeline.run_daily import run_daily_pipeline

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_all_daily() -> dict[str, Any]:
    start_time = time.perf_counter()

    logger.info(
        "nepalstock.com blocks non-Nepal IPs, so nepse_scraper calls are expected to fail with 401 "
        "in CI runners - the sharesansar/merolagani/weekday-pattern fallbacks are the normal path here, not an error"
    )

    try:
        daily_summary = run_daily_pipeline()
    except Exception as exc:
        logger.exception("run_daily.py failed")
        send_discord_alert(f"run_daily.py failed: {exc}", is_failure=True)
        raise

    try:
        quality_summary = check_daily_pipeline_health()
    except Exception as exc:
        logger.exception("data_quality check failed")
        send_discord_alert(f"data_quality check failed: {exc}", is_failure=True)
        raise

    backup_status = "skipped (not a trading day)"
    try:
        if is_market_open_today():
            backup_summary = run_backup()
            backup_status = (
                f"uploaded {backup_summary['backup_filename']} "
                f"(drive id {backup_summary['drive_file_id']}, "
                f"{backup_summary['old_backups_deleted']} old backups pruned)"
            )
    except Exception as exc:
        logger.exception("backup_to_drive.py failed")
        send_discord_alert(f"backup_to_drive.py failed: {exc}", is_failure=True)
        raise

    elapsed_seconds = time.perf_counter() - start_time

    message = (
        f"Daily pipeline completed in {elapsed_seconds:.1f}s\n"
        f"Companies processed: {daily_summary['companies_processed']}\n"
        f"New price rows inserted: {daily_summary['new_price_rows_inserted']}\n"
        f"Duplicates skipped: {daily_summary['duplicates_skipped']}\n"
        f"Price row parse failures: {daily_summary['price_row_parse_failures']}/{daily_summary['raw_price_rows_received']}\n"
        f"Adjustment failures: {daily_summary['adjustment_failures']}/{daily_summary['adjustment_symbols_processed']}\n"
        f"Company upsert failed: {daily_summary['company_upsert_failed']}\n"
        f"Price insert failed: {daily_summary['price_insert_failed']}\n"
        f"Data quality flags: {quality_summary['checks_flagged']}/{quality_summary['checks_run']}\n"
        f"Backup: {backup_status}"
    )

    is_failure = daily_summary["failures"] > 0 or quality_summary["checks_flagged"] > 0
    send_discord_alert(message, is_failure=is_failure)

    logger.info(message.replace("\n", " | "))

    return {
        "daily_summary": daily_summary,
        "quality_summary": quality_summary,
        "backup_status": backup_status,
        "execution_time_seconds": round(elapsed_seconds, 2),
    }


if __name__ == "__main__":
    run_all_daily()
