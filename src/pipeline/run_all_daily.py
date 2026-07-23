from __future__ import annotations

import logging
import time
from typing import Any

from src.notifications.discord_alert import send_discord_alert
from src.pipeline.backfill_calendar import is_market_open_today
from src.pipeline.backfill_signals import run_signals_backfill
from src.pipeline.backup_to_drive import run_backup
from src.pipeline.data_quality import check_daily_pipeline_health
from src.pipeline.run_daily import run_daily_pipeline

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MINOR_FAILURE_THRESHOLD = 5


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
        send_discord_alert(f"run_daily.py failed: {exc}", severity="failure")
        raise

    try:
        quality_summary = check_daily_pipeline_health()
    except Exception as exc:
        logger.exception("data_quality check failed")
        send_discord_alert(f"data_quality check failed: {exc}", severity="failure")
        raise

    try:
        signals_summary = run_signals_backfill()
    except Exception:
        logger.exception("compute_signals.py backfill failed")
        signals_summary = {"symbols_processed": 0, "rows_upserted": 0, "failures": 0}

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
        send_discord_alert(f"backup_to_drive.py failed: {exc}", severity="failure")
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
        f"Signals computed: {signals_summary['symbols_processed']} symbols, "
        f"{signals_summary['rows_upserted']} rows, {signals_summary['failures']} failures\n"
        f"Backup: {backup_status}"
    )

    total_failures = daily_summary["failures"] + signals_summary["failures"]
    if total_failures == 0 and quality_summary["checks_flagged"] == 0:
        severity = "success"
    elif total_failures <= MINOR_FAILURE_THRESHOLD:
        severity = "warning"
    else:
        severity = "failure"
    send_discord_alert(message, severity=severity)

    logger.info(message.replace("\n", " | "))

    return {
        "daily_summary": daily_summary,
        "quality_summary": quality_summary,
        "signals_summary": signals_summary,
        "backup_status": backup_status,
        "execution_time_seconds": round(elapsed_seconds, 2),
    }


if __name__ == "__main__":
    run_all_daily()
