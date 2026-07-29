from __future__ import annotations

import itertools
import logging
import time
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import BacktestResult, DailyPrice, VolumeConfirmedBacktestResult
from src.pipeline.backtest_signals import DEFAULT_FORWARD_DAYS, backtest_multiple_signals
from src.pipeline.compute_baseline import BASELINE_SIGNAL_NAME
from src.pipeline.indicators import sma
from src.pipeline.run_signal_backtests import PATTERN_COLUMNS, _pattern_condition

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

VOLUME_HIGH_THRESHOLD = Decimal("1.2")
VOLUME_SMA_PERIOD = 20
RETRY_ATTEMPTS = 6
RETRY_BASE_DELAY_SECONDS = 5


def retry_with_backoff(fn, *args, **kwargs):
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            return fn(*args, **kwargs)
        except Exception:
            if attempt == RETRY_ATTEMPTS:
                raise
            delay = RETRY_BASE_DELAY_SECONDS * attempt
            logger.warning(
                "%s failed on attempt %d/%d, retrying in %ds",
                getattr(fn, "__name__", str(fn)),
                attempt,
                RETRY_ATTEMPTS,
                delay,
            )
            time.sleep(delay)


def _load_volume_ratios() -> dict[tuple[str, Any], Decimal | None]:
    with get_session() as session:
        rows = session.execute(
            select(DailyPrice.symbol, DailyPrice.date, DailyPrice.volume).order_by(
                DailyPrice.symbol, DailyPrice.date
            )
        ).all()

    ratios: dict[tuple[str, Any], Decimal | None] = {}
    for symbol, group in itertools.groupby(rows, key=lambda r: r.symbol):
        group_rows = list(group)
        dates = [r.date for r in group_rows]
        volumes = [r.volume for r in group_rows]
        volume_sma = sma(volumes, VOLUME_SMA_PERIOD)
        for i, date_ in enumerate(dates):
            avg = volume_sma[i]
            if avg is None or avg == 0:
                ratios[(symbol, date_)] = None
            else:
                ratios[(symbol, date_)] = Decimal(volumes[i]) / avg
    return ratios


def _build_volume_conditions(volume_ratios: dict[tuple[str, Any], Decimal | None]) -> dict[str, Any]:
    conditions = {}
    for column in PATTERN_COLUMNS:
        pattern_fn = _pattern_condition(column)

        def high_volume(row, close, prev_row, prev_close, pattern_fn=pattern_fn):
            if not pattern_fn(row, close, prev_row, prev_close):
                return False
            ratio = volume_ratios.get((row.symbol, row.date))
            return ratio is not None and ratio > VOLUME_HIGH_THRESHOLD

        def normal_volume(row, close, prev_row, prev_close, pattern_fn=pattern_fn):
            if not pattern_fn(row, close, prev_row, prev_close):
                return False
            ratio = volume_ratios.get((row.symbol, row.date))
            return ratio is not None and ratio <= VOLUME_HIGH_THRESHOLD

        conditions[f"{column}|high"] = high_volume
        conditions[f"{column}|normal"] = normal_volume
    return conditions


def _load_baseline_win_rate_by_horizon() -> dict[int, Any]:
    with get_session() as session:
        rows = session.execute(
            select(BacktestResult.forward_days, BacktestResult.win_rate).where(
                BacktestResult.signal_name == BASELINE_SIGNAL_NAME
            )
        ).all()
        return {r.forward_days: r.win_rate for r in rows}


def _upsert_volume_confirmed_results(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(VolumeConfirmedBacktestResult).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["pattern_name", "volume_condition", "forward_days"],
        set_={
            "sample_size": stmt.excluded.sample_size,
            "win_rate": stmt.excluded.win_rate,
            "win_rate_minus_baseline": stmt.excluded.win_rate_minus_baseline,
            "computed_at": stmt.excluded.computed_at,
        },
    )
    with get_session() as session:
        session.execute(stmt)
    return len(rows)


def run_volume_confirmation_backtest(forward_days: list[int] = DEFAULT_FORWARD_DAYS) -> dict[str, Any]:
    logger.info("Loading volume ratios (volume / %d-day trailing avg volume)", VOLUME_SMA_PERIOD)
    volume_ratios = retry_with_backoff(_load_volume_ratios)

    conditions = _build_volume_conditions(volume_ratios)
    logger.info("Running volume-confirmed backtest for %d pattern columns x 2 volume conditions", len(PATTERN_COLUMNS))
    results = retry_with_backoff(backtest_multiple_signals, conditions, forward_days)

    baseline_win_rate_by_horizon = retry_with_backoff(_load_baseline_win_rate_by_horizon)
    computed_at = datetime.utcnow()

    rows = []
    for column in PATTERN_COLUMNS:
        for volume_condition in ("high", "normal"):
            key = f"{column}|{volume_condition}"
            pattern_result = results[key]
            for n in forward_days:
                stats = pattern_result["forward_days"][n]
                baseline_win_rate = baseline_win_rate_by_horizon.get(n)
                win_rate_minus_baseline = None
                if stats["win_rate"] is not None and baseline_win_rate is not None:
                    win_rate_minus_baseline = stats["win_rate"] - baseline_win_rate

                rows.append(
                    {
                        "pattern_name": column,
                        "volume_condition": volume_condition,
                        "forward_days": n,
                        "sample_size": stats["sample_size"],
                        "win_rate": stats["win_rate"],
                        "win_rate_minus_baseline": win_rate_minus_baseline,
                        "computed_at": computed_at,
                    }
                )

    stored = retry_with_backoff(_upsert_volume_confirmed_results, rows)
    logger.info("Stored %d volume_confirmed_backtest_results rows", stored)

    return {"patterns_tested": len(PATTERN_COLUMNS), "rows_stored": stored, "results": results}


if __name__ == "__main__":
    run_volume_confirmation_backtest()
