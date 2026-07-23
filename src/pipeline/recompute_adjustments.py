from __future__ import annotations

import logging
import time
from typing import Any

from src.pipeline.adjustment_ops import reapply_adjustment_for_symbol, symbols_with_corporate_actions

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROGRESS_LOG_INTERVAL = 20


def run_recompute_adjustments() -> dict[str, Any]:
    start_time = time.perf_counter()

    symbols = symbols_with_corporate_actions()
    logger.info("Recomputing adjustments for %d symbols with corporate actions", len(symbols))

    symbols_processed = 0
    rows_updated_total = 0
    failures = 0

    for symbol in symbols:
        symbols_processed += 1
        try:
            rows_updated = reapply_adjustment_for_symbol(symbol)
            rows_updated_total += rows_updated
        except Exception:
            logger.exception("Failed to recompute adjustment for symbol %s", symbol)
            failures += 1

        if symbols_processed % PROGRESS_LOG_INTERVAL == 0:
            logger.info(
                "Progress: %d/%d symbols done, rows_updated=%d failures=%d",
                symbols_processed,
                len(symbols),
                rows_updated_total,
                failures,
            )

    elapsed_seconds = time.perf_counter() - start_time

    summary = {
        "symbols_processed": symbols_processed,
        "rows_updated": rows_updated_total,
        "failures": failures,
        "execution_time_seconds": round(elapsed_seconds, 2),
    }

    logger.info(
        "Recompute adjustments summary: symbols_processed=%d rows_updated=%d failures=%d execution_time_seconds=%.2f",
        summary["symbols_processed"],
        summary["rows_updated"],
        summary["failures"],
        summary["execution_time_seconds"],
    )

    return summary


if __name__ == "__main__":
    run_recompute_adjustments()
