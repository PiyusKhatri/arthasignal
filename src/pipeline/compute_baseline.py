from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from src.pipeline.backtest_signals import DEFAULT_FORWARD_DAYS, backtest_indicator_signal
from src.pipeline.run_signal_backtests import _build_result_rows, _upsert_backtest_results

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASELINE_SIGNAL_NAME = "baseline_unconditional"


def _always_true(row, close, prev_row, prev_close) -> bool:
    return True


def compute_baseline(forward_days: list[int] = DEFAULT_FORWARD_DAYS) -> dict[str, Any]:
    logger.info("Computing unconditional baseline forward returns across all symbols and dates")

    result = backtest_indicator_signal(
        BASELINE_SIGNAL_NAME,
        _always_true,
        forward_days=forward_days,
        dedup_episodes=False,
    )

    computed_at = datetime.utcnow()
    rows = _build_result_rows({BASELINE_SIGNAL_NAME: result}, computed_at)
    stored = _upsert_backtest_results(rows)

    logger.info("Stored %d baseline_unconditional rows", stored)
    return {"rows_stored": stored, "result": result}


if __name__ == "__main__":
    compute_baseline()
