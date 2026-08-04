from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any

from src.notifications.discord_alert import send_discord_alert
from src.pipeline.backfill_calendar import is_market_open_today, run_calendar_backfill
from src.pipeline.backfill_daily_floorsheet import run_daily_floorsheet_backfill
from src.pipeline.backfill_daily_index import run_daily_index_refresh
from src.pipeline.backfill_signals import run_signals_backfill
from src.pipeline.backup_to_drive import run_backup
from src.pipeline.cleanup_intraday_tables import run_intraday_table_cleanup
from src.pipeline.data_quality import check_daily_pipeline_health
from src.pipeline.extract_signal_calls import extract_signal_calls
from src.pipeline.grade_signal_calls import grade_signal_calls
from src.pipeline.refresh_ipo_status import refresh_ipo_status
from src.pipeline.run_daily import run_daily_pipeline

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MINOR_FAILURE_THRESHOLD = 5
SIGNAL_CALL_EXTRACTION_LOOKBACK_DAYS = 7


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
        calendar_summary = run_calendar_backfill(attempt_confirmed_for_today=True)
    except Exception as exc:
        logger.exception("backfill_calendar.py failed")
        send_discord_alert(f"backfill_calendar.py failed: {exc}", severity="failure")
        calendar_summary = {
            "total_days_processed": 0,
            "rows_written": 0,
            "trading_days": 0,
            "non_trading_days": 0,
            "unexplained_non_trading_days": 0,
            "today_row_written": False,
            "failures": 1,
        }

    try:
        quality_summary = check_daily_pipeline_health()
    except Exception as exc:
        logger.exception("data_quality check failed")
        send_discord_alert(f"data_quality check failed: {exc}", severity="failure")
        raise

    ingestion_gap = quality_summary.get("results", {}).get("trading_day_ingestion_gap", {})
    if ingestion_gap.get("flagged"):
        send_discord_alert(
            "SILENT INGESTION FAILURE SUSPECTED\n"
            f"Date: {ingestion_gap.get('date')}\n"
            f"Trading calendar row present: {ingestion_gap.get('calendar_row_present')}\n"
            f"daily_prices rows today: {ingestion_gap.get('daily_price_rows_today')}\n"
            f"intraday_snapshots rows today: {ingestion_gap.get('intraday_snapshot_rows_today')}\n"
            + "\n".join(ingestion_gap.get("reasons", [])),
            severity="failure",
        )

    try:
        signals_summary = run_signals_backfill()
    except Exception:
        logger.exception("compute_signals.py backfill failed")
        signals_summary = {"symbols_processed": 0, "rows_upserted": 0, "failures": 0}

    try:
        extraction_summary = extract_signal_calls(
            date.today() - timedelta(days=SIGNAL_CALL_EXTRACTION_LOOKBACK_DAYS), date.today()
        )
    except Exception as exc:
        logger.exception("extract_signal_calls.py failed")
        send_discord_alert(f"Paper-trade validation alert: extract_signal_calls.py failed: {exc}", severity="failure")
        extraction_summary = {
            "rows_extracted": 0,
            "rows_inserted": 0,
            "skipped_missing_next_day_price": 0,
            "by_signal": {},
            "failures": 1,
        }

    try:
        grading_summary = grade_signal_calls()
    except Exception as exc:
        logger.exception("grade_signal_calls.py failed")
        send_discord_alert(f"Paper-trade validation alert: grade_signal_calls.py failed: {exc}", severity="failure")
        grading_summary = {
            "total_pending": 0,
            "not_ready": 0,
            "resolved": 0,
            "voided": 0,
            "win": 0,
            "loss": 0,
            "failures": 1,
        }

    by_signal_str = ", ".join(f"{k}={v}" for k, v in extraction_summary.get("by_signal", {}).items())
    extraction_status = f"{extraction_summary.get('rows_inserted', 0)} new calls ({by_signal_str})"
    grading_status = (
        f"{grading_summary.get('resolved', 0)} resolved "
        f"({grading_summary.get('win', 0)} WIN / {grading_summary.get('loss', 0)} LOSS), "
        f"{grading_summary.get('voided', 0)} VOID"
    )

    try:
        floorsheet_summary = run_daily_floorsheet_backfill()
    except Exception:
        logger.exception("backfill_daily_floorsheet.py failed")
        floorsheet_summary = {"skipped": False, "symbols_processed": 0, "rows_inserted": 0, "failures": 0}

    if floorsheet_summary.get("skipped"):
        floorsheet_status = "skipped (not a trading day)"
    else:
        floorsheet_status = (
            f"{floorsheet_summary.get('symbols_processed', 0)} symbols, "
            f"{floorsheet_summary.get('rows_inserted', 0)} rows, "
            f"{floorsheet_summary.get('failures', 0)} failures"
        )

    try:
        index_summary = run_daily_index_refresh()
    except Exception:
        logger.exception("backfill_daily_index.py failed")
        index_summary = {"skipped": False, "indices_processed": 0, "rows_upserted": 0, "failures": 0}

    if index_summary.get("skipped"):
        index_status = "skipped (not a trading day)"
    else:
        index_status = (
            f"{index_summary.get('indices_processed', 0)} indices, "
            f"{index_summary.get('rows_upserted', 0)} rows, "
            f"{index_summary.get('failures', 0)} failures"
        )

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

    try:
        cleanup_summary = run_intraday_table_cleanup()
    except Exception:
        logger.exception("cleanup_intraday_tables.py failed")
        cleanup_summary = {"total_deleted": 0, "deleted_by_table": {}, "failures": 1}

    cleanup_status = (
        f"{cleanup_summary.get('total_deleted', 0)} rows deleted "
        f"({cleanup_summary.get('failures', 0)} table failures)"
    )

    try:
        ipo_status_summary = refresh_ipo_status()
    except Exception:
        logger.exception("refresh_ipo_status.py failed")
        ipo_status_summary = {"rows_checked": 0, "rows_updated": 0, "failures": 1}

    ipo_status_line = (
        f"{ipo_status_summary.get('rows_checked', 0)} checked, "
        f"{ipo_status_summary.get('rows_updated', 0)} updated"
    )

    elapsed_seconds = time.perf_counter() - start_time

    if daily_summary.get("skipped"):
        daily_price_section = "New price rows inserted: skipped (not a trading day)"
    else:
        daily_price_section = (
            f"Companies processed: {daily_summary['companies_processed']}\n"
            f"New price rows inserted: {daily_summary['new_price_rows_inserted']}\n"
            f"Duplicates skipped: {daily_summary['duplicates_skipped']}\n"
            f"Price row parse failures: {daily_summary['price_row_parse_failures']}/{daily_summary['raw_price_rows_received']}\n"
            f"Adjustment failures: {daily_summary['adjustment_failures']}/{daily_summary['adjustment_symbols_processed']}\n"
            f"Company upsert failed: {daily_summary['company_upsert_failed']}\n"
            f"Price insert failed: {daily_summary['price_insert_failed']}"
        )

    calendar_status = (
        f"{calendar_summary.get('rows_written', 0)} rows written, "
        f"today_row_written={calendar_summary.get('today_row_written', False)}, "
        f"{calendar_summary.get('unexplained_non_trading_days', 0)} unexplained non-trading days"
    )

    message = (
        f"Daily pipeline completed in {elapsed_seconds:.1f}s\n"
        f"{daily_price_section}\n"
        f"Trading calendar refresh: {calendar_status}\n"
        f"Data quality flags: {quality_summary['checks_flagged']}/{quality_summary['checks_run']}\n"
        f"Signals computed: {signals_summary['symbols_processed']} symbols, "
        f"{signals_summary['rows_upserted']} rows, {signals_summary['failures']} failures\n"
        f"Signal call extraction: {extraction_status}\n"
        f"Signal call grading: {grading_status}\n"
        f"Floorsheet: {floorsheet_status}\n"
        f"Index refresh: {index_status}\n"
        f"Backup: {backup_status}\n"
        f"Intraday cleanup: {cleanup_status}\n"
        f"IPO status refresh: {ipo_status_line}"
    )

    total_failures = (
        daily_summary["failures"]
        + calendar_summary.get("failures", 0)
        + signals_summary["failures"]
        + extraction_summary.get("failures", 0)
        + grading_summary.get("failures", 0)
        + floorsheet_summary.get("failures", 0)
        + index_summary.get("failures", 0)
        + cleanup_summary.get("failures", 0)
        + ipo_status_summary.get("failures", 0)
    )
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
        "calendar_summary": calendar_summary,
        "quality_summary": quality_summary,
        "signals_summary": signals_summary,
        "extraction_summary": extraction_summary,
        "grading_summary": grading_summary,
        "floorsheet_summary": floorsheet_summary,
        "index_summary": index_summary,
        "backup_status": backup_status,
        "cleanup_summary": cleanup_summary,
        "ipo_status_summary": ipo_status_summary,
        "execution_time_seconds": round(elapsed_seconds, 2),
    }


if __name__ == "__main__":
    run_all_daily()
