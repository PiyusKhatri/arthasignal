from __future__ import annotations

import logging
from typing import Any

from src.database.models import IntradayIndexSnapshot, IntradaySnapshot
from src.notifications.discord_alert import send_discord_alert
from src.pipeline.backfill_intraday_index_snapshots import run_intraday_index_snapshot_backfill
from src.pipeline.backfill_intraday_snapshots import run_intraday_snapshot_backfill
from src.pipeline.intraday_data_quality import check_intraday_data_quality, detect_intraday_gap
from src.pipeline.market_hours_guard import _current_npt_time, is_within_intraday_window

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _check_for_gap() -> dict[str, Any]:
    if not is_within_intraday_window():
        return {}

    now_npt = _current_npt_time()
    gaps = {
        "price": detect_intraday_gap(IntradaySnapshot, "intraday_snapshots", now_npt),
        "index": detect_intraday_gap(IntradayIndexSnapshot, "intraday_index_snapshots", now_npt),
    }

    for gap in gaps.values():
        if gap.get("gap_detected"):
            logger.warning(
                "CATCH-UP RUN: %.1f minutes since the last %s row for today - proceeding with a normal "
                "fetch, flagging this explicitly so the gap is visible in monitoring rather than silent",
                gap["minutes_since_last_snapshot"],
                gap["table"],
            )

    return gaps


def run_intraday_pipeline() -> dict[str, Any]:
    gap_summary = _check_for_gap()

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

    logger.info(
        "Intraday pipeline summary: price=%s index=%s gap_check=%s", price_summary, index_summary, gap_summary
    )

    try:
        quality_summary = check_intraday_data_quality(price_summary, index_summary)
    except Exception:
        logger.exception("check_intraday_data_quality failed")
        quality_summary = {"run_checks": {}, "daily_coverage": {}, "should_alert": False}

    if quality_summary["should_alert"]:
        coverage = quality_summary["daily_coverage"]
        zero_coverage = any(v["realized_snapshots"] == 0 for v in coverage.values())
        midday_reached = any(v.get("midday_checkpoint_reached") for v in coverage.values())
        lines = [
            f"{v['table']}: {v['realized_snapshots']}/{v['expected_snapshots_so_far']} snapshots today "
            f"({(v['coverage_ratio'] or 0) * 100:.0f}%)"
            for v in coverage.values()
        ]
        gap_lines = [
            f"{gap['table']}: {gap['minutes_since_last_snapshot']} min since last row"
            for gap in gap_summary.values()
            if gap.get("gap_detected")
        ]
        message_lines = lines + (["Gaps detected:"] + gap_lines if gap_lines else [])

        if zero_coverage:
            send_discord_alert(
                "ZERO INTRADAY SNAPSHOTS - possible silent pipeline failure, not just low coverage:\n"
                + "\n".join(message_lines),
                severity="failure",
            )
        elif midday_reached:
            send_discord_alert(
                "MIDDAY COVERAGE CHECK (as of 13:00 NPT checkpoint) - coverage is still low partway "
                "through the session, not just in an end-of-day summary:\n" + "\n".join(message_lines),
                severity="failure",
            )
        else:
            send_discord_alert("Intraday snapshot coverage is low today:\n" + "\n".join(message_lines), severity="warning")

    return {
        "price_summary": price_summary,
        "index_summary": index_summary,
        "quality_summary": quality_summary,
        "gap_summary": gap_summary,
    }


if __name__ == "__main__":
    run_intraday_pipeline()
