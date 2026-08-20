from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any

from src.notifications.discord_alert import send_discord_alert
from src.pipeline.backfill_calendar import is_trading_day, run_calendar_backfill
from src.pipeline.backfill_daily_floorsheet import run_daily_floorsheet_backfill
from src.pipeline.backfill_daily_index import run_daily_index_refresh
from src.pipeline.backfill_signals import run_signals_backfill
from src.pipeline.backup_to_drive import run_backup
from src.pipeline.cleanup_intraday_tables import run_intraday_table_cleanup
from src.pipeline.compute_liquidity_tiers import compute_liquidity_tiers
from src.pipeline.data_quality import check_daily_pipeline_health
from src.pipeline.extract_signal_calls import extract_signal_calls
from src.pipeline.grade_signal_calls import grade_signal_calls
from src.pipeline.refresh_ipo_status import refresh_ipo_status
from src.pipeline.run_daily import run_daily_pipeline
from src.pipeline.sync_market_data import repair_market_gaps, should_auto_repair_gaps

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MINOR_FAILURE_THRESHOLD = 5
SIGNAL_CALL_EXTRACTION_LOOKBACK_DAYS = 7


def _safe_failure_summary(**values: Any) -> dict[str, Any]:
    return {"failures": 1, **values}


def run_all_daily() -> dict[str, Any]:
    start_time = time.perf_counter()

    logger.info(
        "Starting gap-aware daily pipeline. Live NEPSE endpoints may be unavailable from non-Nepal runners; "
        "existing scraper fallbacks remain enabled."
    )

    gap_summary: dict[str, Any] | None = None
    try:
        if should_auto_repair_gaps():
            logger.warning("Detected stale/missing market history; repairing price and index gaps before EOD refresh")
            gap_summary = repair_market_gaps()
        else:
            gap_summary = {"skipped": True, "reason": "no historical gap detected"}
    except Exception as exc:
        logger.exception("Historical gap repair failed")
        send_discord_alert(f"Historical market-data gap repair failed: {exc}", severity="failure")
        gap_summary = _safe_failure_summary(reason="gap repair failed")

    try:
        daily_summary = run_daily_pipeline()
    except Exception as exc:
        logger.exception("run_daily.py failed")
        send_discord_alert(f"run_daily.py failed: {exc}", severity="failure")
        raise

    try:
        index_summary = run_daily_index_refresh()
    except Exception:
        logger.exception("backfill_daily_index.py failed")
        index_summary = _safe_failure_summary(
            skipped=False,
            indices_processed=0,
            rows_upserted=0,
        )

    try:
        calendar_summary = run_calendar_backfill(attempt_confirmed_for_today=True)
    except Exception as exc:
        logger.exception("backfill_calendar.py failed")
        send_discord_alert(f"backfill_calendar.py failed: {exc}", severity="failure")
        calendar_summary = _safe_failure_summary(
            total_days_processed=0,
            rows_written=0,
            trading_days=0,
            non_trading_days=0,
            unexplained_non_trading_days=0,
            repaired_unexplained_false_rows=0,
            today_row_written=False,
        )

    try:
        signals_summary = run_signals_backfill()
    except Exception:
        logger.exception("compute_signals.py backfill failed")
        signals_summary = _safe_failure_summary(symbols_processed=0, rows_upserted=0)

    try:
        liquidity_summary = compute_liquidity_tiers()
    except Exception:
        logger.exception("compute_liquidity_tiers.py failed")
        liquidity_summary = {"symbols_tiered": 0, "tier_counts": {}, "failures": 1}

    try:
        extraction_summary = extract_signal_calls(
            date.today() - timedelta(days=SIGNAL_CALL_EXTRACTION_LOOKBACK_DAYS),
            date.today(),
        )
    except Exception as exc:
        logger.exception("extract_signal_calls.py failed")
        send_discord_alert(
            f"Paper-trade validation alert: extract_signal_calls.py failed: {exc}",
            severity="failure",
        )
        extraction_summary = _safe_failure_summary(
            rows_extracted=0,
            rows_inserted=0,
            skipped_missing_next_day_price=0,
            by_signal={},
        )

    try:
        grading_summary = grade_signal_calls()
    except Exception as exc:
        logger.exception("grade_signal_calls.py failed")
        send_discord_alert(
            f"Paper-trade validation alert: grade_signal_calls.py failed: {exc}",
            severity="failure",
        )
        grading_summary = _safe_failure_summary(
            total_pending=0,
            not_ready=0,
            resolved=0,
            voided=0,
            win=0,
            loss=0,
        )

    try:
        floorsheet_summary = run_daily_floorsheet_backfill()
    except Exception:
        logger.exception("backfill_daily_floorsheet.py failed")
        floorsheet_summary = _safe_failure_summary(
            skipped=False,
            symbols_processed=0,
            rows_inserted=0,
        )

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

    backup_status = "skipped (not a trading day)"
    try:
        if is_trading_day():
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
        cleanup_summary = _safe_failure_summary(total_deleted=0, deleted_by_table={})

    try:
        ipo_status_summary = refresh_ipo_status()
    except Exception:
        logger.exception("refresh_ipo_status.py failed")
        ipo_status_summary = _safe_failure_summary(rows_checked=0, rows_updated=0)

    gap_failures = 0
    if gap_summary:
        if "failures" in gap_summary:
            gap_failures += int(gap_summary.get("failures", 0))
        gap_failures += int(gap_summary.get("prices", {}).get("failures", 0))
        gap_failures += int(gap_summary.get("indices", {}).get("failures", 0))

    total_failures = (
        gap_failures
        + int(daily_summary.get("failures", 0))
        + int(calendar_summary.get("failures", 0))
        + int(signals_summary.get("failures", 0))
        + int(liquidity_summary.get("failures", 0))
        + int(extraction_summary.get("failures", 0))
        + int(grading_summary.get("failures", 0))
        + int(floorsheet_summary.get("failures", 0))
        + int(index_summary.get("failures", 0))
        + int(cleanup_summary.get("failures", 0))
        + int(ipo_status_summary.get("failures", 0))
    )

    elapsed_seconds = time.perf_counter() - start_time
    message = (
        f"Gap-aware daily pipeline completed in {elapsed_seconds:.1f}s\n"
        f"Gap repair: {gap_summary}\n"
        f"Daily prices: {daily_summary}\n"
        f"Index refresh: {index_summary}\n"
        f"Calendar: {calendar_summary}\n"
        f"Signals: {signals_summary}\n"
        f"Liquidity: {liquidity_summary}\n"
        f"Data quality flags: {quality_summary['checks_flagged']}/{quality_summary['checks_run']}\n"
        f"Backup: {backup_status}"
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
        "gap_summary": gap_summary,
        "daily_summary": daily_summary,
        "calendar_summary": calendar_summary,
        "quality_summary": quality_summary,
        "signals_summary": signals_summary,
        "liquidity_summary": liquidity_summary,
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
