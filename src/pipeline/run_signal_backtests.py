from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import BacktestResult
from src.pipeline.backtest_signals import DEFAULT_FORWARD_DAYS, backtest_multiple_signals

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PATTERN_COLUMNS = [
    "doji",
    "marubozu_bullish",
    "marubozu_bearish",
    "hammer",
    "shooting_star",
    "spinning_top",
    "bullish_engulfing",
    "bearish_engulfing",
    "bullish_harami",
    "bearish_harami",
    "piercing_line",
    "dark_cloud_cover",
    "tweezer_top",
    "tweezer_bottom",
    "morning_star",
    "evening_star",
    "three_white_soldiers",
    "three_black_crows",
]


def _pattern_condition(column: str):
    return lambda row, close, prev_row, prev_close: bool(getattr(row, column))


def _macd_bullish_crossover(row, close, prev_row, prev_close) -> bool:
    if prev_row is None:
        return False
    fields = (row.macd_line, row.macd_signal, prev_row.macd_line, prev_row.macd_signal)
    if any(f is None for f in fields):
        return False
    return prev_row.macd_line <= prev_row.macd_signal and row.macd_line > row.macd_signal


def _macd_bearish_crossover(row, close, prev_row, prev_close) -> bool:
    if prev_row is None:
        return False
    fields = (row.macd_line, row.macd_signal, prev_row.macd_line, prev_row.macd_signal)
    if any(f is None for f in fields):
        return False
    return prev_row.macd_line >= prev_row.macd_signal and row.macd_line < row.macd_signal


def build_signal_conditions() -> dict[str, Any]:
    conditions = {
        "rsi_14 < 30 (oversold)": lambda row, close, prev_row, prev_close: (
            row.rsi_14 is not None and row.rsi_14 < 30
        ),
        "rsi_14 > 70 (overbought)": lambda row, close, prev_row, prev_close: (
            row.rsi_14 is not None and row.rsi_14 > 70
        ),
        "macd bullish crossover": _macd_bullish_crossover,
        "macd bearish crossover": _macd_bearish_crossover,
        "close < bollinger_lower": lambda row, close, prev_row, prev_close: (
            close is not None and row.bollinger_lower is not None and close < row.bollinger_lower
        ),
        "close > bollinger_upper": lambda row, close, prev_row, prev_close: (
            close is not None and row.bollinger_upper is not None and close > row.bollinger_upper
        ),
        "stochastic_k < 20": lambda row, close, prev_row, prev_close: (
            row.stochastic_k is not None and row.stochastic_k < 20
        ),
        "stochastic_k > 80": lambda row, close, prev_row, prev_close: (
            row.stochastic_k is not None and row.stochastic_k > 80
        ),
    }
    for column in PATTERN_COLUMNS:
        conditions[column] = _pattern_condition(column)
    return conditions


def _build_result_rows(results: dict[str, Any], computed_at: datetime) -> list[dict[str, Any]]:
    rows = []
    for signal_name, result in results.items():
        for forward_days, stats in result["forward_days"].items():
            rows.append(
                {
                    "signal_name": signal_name,
                    "forward_days": forward_days,
                    "sample_size": stats["sample_size"],
                    "mean_return": stats["mean_forward_return"],
                    "median_return": stats["median_forward_return"],
                    "win_rate": stats["win_rate"],
                    "std_dev": stats["stddev_forward_return"],
                    "computed_at": computed_at,
                }
            )
    return rows


def _upsert_backtest_results(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(BacktestResult).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["signal_name", "forward_days"],
        set_={
            "sample_size": stmt.excluded.sample_size,
            "mean_return": stmt.excluded.mean_return,
            "median_return": stmt.excluded.median_return,
            "win_rate": stmt.excluded.win_rate,
            "std_dev": stmt.excluded.std_dev,
            "computed_at": stmt.excluded.computed_at,
        },
    )
    with get_session() as session:
        session.execute(stmt)
    return len(rows)


def run_all_signal_backtests(forward_days: list[int] = DEFAULT_FORWARD_DAYS) -> dict[str, Any]:
    conditions = build_signal_conditions()
    logger.info("Running %d signal backtests across forward_days=%s", len(conditions), forward_days)

    results = backtest_multiple_signals(conditions, forward_days)
    computed_at = datetime.utcnow()
    rows = _build_result_rows(results, computed_at)
    stored = _upsert_backtest_results(rows)

    logger.info("Stored %d backtest_results rows for %d signals", stored, len(conditions))
    return {"signals_tested": len(conditions), "rows_stored": stored, "results": results}


if __name__ == "__main__":
    run_all_signal_backtests()
