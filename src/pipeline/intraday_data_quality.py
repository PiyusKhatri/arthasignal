from __future__ import annotations

import logging
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import func, select, text

from src.database.connection import get_session
from src.database.models import IntradayIndexSnapshot, IntradaySnapshot
from src.pipeline.backfill_intraday_snapshots import SNAPSHOT_ROUND_MINUTES
from src.pipeline.market_hours_guard import SESSION_END, SESSION_START, _current_npt_time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SYMBOL_COVERAGE_MIN_RATIO = 0.9
EXPECTED_INDEX_COUNT = 17
# Distinct snapshot_time rows land on SNAPSHOT_ROUND_MINUTES boundaries (15 min), not on every
# 5-minute cron trigger - using the cron interval here previously understated real coverage by
# ~3x and risked constant false "low coverage" alerts even under healthy operation.
SNAPSHOT_INTERVAL_MINUTES = SNAPSHOT_ROUND_MINUTES
COVERAGE_ALERT_RATIO_THRESHOLD = 0.3
COVERAGE_ALERT_MIN_ELAPSED_RATIO = 0.5
MIDDAY_CHECKPOINT_TIME = time(13, 0)
GAP_CHECK_THRESHOLD_MINUTES = SNAPSHOT_ROUND_MINUTES + 5


def _active_equity_symbol_count() -> int:
    with get_session() as session:
        return session.execute(
            text("SELECT count(*) FROM companies WHERE status = 'A' AND instrument_type = 'Equity'")
        ).scalar()


def check_snapshot_run_coverage(symbols_fetched: int) -> dict[str, Any]:
    expected = _active_equity_symbol_count()
    ratio = symbols_fetched / expected if expected else None
    flagged = ratio is not None and ratio < SYMBOL_COVERAGE_MIN_RATIO

    if flagged:
        logger.warning(
            "intraday_data_quality: symbol run coverage low - fetched %d of %d expected active symbols (%.1f%%)",
            symbols_fetched,
            expected,
            ratio * 100,
        )

    return {"flagged": flagged, "symbols_fetched": symbols_fetched, "expected_symbol_count": expected, "ratio": ratio}


def check_index_run_coverage(indices_fetched: int) -> dict[str, Any]:
    ratio = indices_fetched / EXPECTED_INDEX_COUNT
    flagged = ratio < SYMBOL_COVERAGE_MIN_RATIO

    if flagged:
        logger.warning(
            "intraday_data_quality: index run coverage low - fetched %d of %d expected indices (%.1f%%)",
            indices_fetched,
            EXPECTED_INDEX_COUNT,
            ratio * 100,
        )

    return {"flagged": flagged, "indices_fetched": indices_fetched, "expected_index_count": EXPECTED_INDEX_COUNT, "ratio": ratio}


def _distinct_snapshot_times_today(model: Any, today: date) -> list[datetime]:
    with get_session() as session:
        rows = session.execute(
            select(model.snapshot_time)
            .distinct()
            .where(func.date(model.snapshot_time) == today)
            .order_by(model.snapshot_time)
        ).all()
    return [r[0] for r in rows]


def _elapsed_session_minutes(now_npt: datetime) -> float:
    session_start_dt = now_npt.replace(hour=SESSION_START.hour, minute=SESSION_START.minute, second=0, microsecond=0)
    session_end_dt = now_npt.replace(hour=SESSION_END.hour, minute=SESSION_END.minute, second=0, microsecond=0)

    if now_npt < session_start_dt:
        return 0.0

    clamped_now = min(now_npt, session_end_dt)
    return (clamped_now - session_start_dt).total_seconds() / 60


def _full_session_minutes(now_npt: datetime) -> float:
    session_start_dt = now_npt.replace(hour=SESSION_START.hour, minute=SESSION_START.minute, second=0, microsecond=0)
    session_end_dt = now_npt.replace(hour=SESSION_END.hour, minute=SESSION_END.minute, second=0, microsecond=0)
    return (session_end_dt - session_start_dt).total_seconds() / 60


def compute_daily_snapshot_coverage(model: Any, table_name: str, today: date, now_npt: datetime) -> dict[str, Any]:
    snapshot_times = _distinct_snapshot_times_today(model, today)
    realized = len(snapshot_times)

    elapsed_minutes = _elapsed_session_minutes(now_npt)
    expected = int(elapsed_minutes // SNAPSHOT_INTERVAL_MINUTES) + 1 if elapsed_minutes > 0 else 0
    coverage_ratio = realized / expected if expected else None

    elapsed_ratio = elapsed_minutes / _full_session_minutes(now_npt)
    should_alert = (
        coverage_ratio is not None
        and coverage_ratio < COVERAGE_ALERT_RATIO_THRESHOLD
        and elapsed_ratio >= COVERAGE_ALERT_MIN_ELAPSED_RATIO
    )
    midday_checkpoint_reached = now_npt.time() >= MIDDAY_CHECKPOINT_TIME

    logger.info(
        "intraday_data_quality: %s coverage for %s - %d/%d expected snapshots so far (%s), elapsed_ratio=%.2f",
        table_name,
        today,
        realized,
        expected,
        f"{coverage_ratio * 100:.1f}%" if coverage_ratio is not None else "n/a",
        elapsed_ratio,
    )

    return {
        "table": table_name,
        "date": today,
        "realized_snapshots": realized,
        "expected_snapshots_so_far": expected,
        "coverage_ratio": coverage_ratio,
        "elapsed_session_ratio": round(elapsed_ratio, 3),
        "should_alert": should_alert,
        "midday_checkpoint_reached": midday_checkpoint_reached,
        "snapshot_times": snapshot_times,
    }


def detect_intraday_gap(model: Any, table_name: str, now_npt: datetime) -> dict[str, Any]:
    today = now_npt.date()
    with get_session() as session:
        last_snapshot_time = session.execute(
            select(func.max(model.snapshot_time)).where(func.date(model.snapshot_time) == today)
        ).scalar()

    if last_snapshot_time is None:
        return {
            "table": table_name,
            "gap_detected": False,
            "minutes_since_last_snapshot": None,
            "reason": "no snapshots recorded yet today",
        }

    minutes_since = (now_npt - last_snapshot_time).total_seconds() / 60
    gap_detected = minutes_since > GAP_CHECK_THRESHOLD_MINUTES

    return {
        "table": table_name,
        "gap_detected": gap_detected,
        "minutes_since_last_snapshot": round(minutes_since, 1),
    }


def check_intraday_data_quality(price_summary: dict[str, Any], index_summary: dict[str, Any]) -> dict[str, Any]:
    now_npt = _current_npt_time()
    today = now_npt.date()

    run_checks = {}
    if not price_summary.get("skipped"):
        run_checks["price_run_coverage"] = check_snapshot_run_coverage(price_summary.get("symbols_fetched", 0))
    if not index_summary.get("skipped"):
        run_checks["index_run_coverage"] = check_index_run_coverage(index_summary.get("indices_fetched", 0))

    daily_coverage = {
        "price_daily_coverage": compute_daily_snapshot_coverage(IntradaySnapshot, "intraday_snapshots", today, now_npt),
        "index_daily_coverage": compute_daily_snapshot_coverage(
            IntradayIndexSnapshot, "intraday_index_snapshots", today, now_npt
        ),
    }

    should_alert = any(v["should_alert"] for v in daily_coverage.values())

    return {"run_checks": run_checks, "daily_coverage": daily_coverage, "should_alert": should_alert}
