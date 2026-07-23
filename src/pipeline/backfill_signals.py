from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import select

from src.database.connection import get_session
from src.database.models import Company
from src.pipeline.compute_signals import compute_and_store_signals

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROGRESS_LOG_INTERVAL = 20


def _active_equity_symbols() -> list[str]:
    with get_session() as session:
        rows = session.execute(
            select(Company.symbol)
            .where(Company.status == "A")
            .where(Company.instrument_type == "Equity")
            .order_by(Company.symbol)
        ).all()
    return [r.symbol for r in rows]


def run_signals_backfill(symbols: list[str] | None = None, timeframe: str = "daily") -> dict[str, Any]:
    start_time = time.perf_counter()

    if symbols is None:
        symbols = _active_equity_symbols()
    logger.info("Computing %s signals for %d symbols", timeframe, len(symbols))

    symbols_processed = 0
    rows_upserted_total = 0
    failures = 0

    for symbol in symbols:
        symbols_processed += 1
        try:
            rows_upserted_total += compute_and_store_signals(symbol, timeframe)
        except Exception:
            logger.exception("Failed to compute signals for symbol %s", symbol)
            failures += 1

        if symbols_processed % PROGRESS_LOG_INTERVAL == 0:
            logger.info(
                "Progress: %d/%d symbols done, rows_upserted=%d failures=%d",
                symbols_processed,
                len(symbols),
                rows_upserted_total,
                failures,
            )

    elapsed_seconds = time.perf_counter() - start_time

    summary = {
        "symbols_processed": symbols_processed,
        "rows_upserted": rows_upserted_total,
        "failures": failures,
        "execution_time_seconds": round(elapsed_seconds, 2),
    }

    logger.info(
        "Signals backfill complete: %d symbols, %d rows upserted, %d failures, %.2fs",
        summary["symbols_processed"],
        summary["rows_upserted"],
        summary["failures"],
        summary["execution_time_seconds"],
    )

    return summary


if __name__ == "__main__":
    run_signals_backfill()
