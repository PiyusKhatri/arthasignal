from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import IntradayFloorsheet
from src.pipeline.backfill_calendar import is_market_open_today
from src.pipeline.market_hours_guard import NPT_OFFSET, SESSION_END, _current_npt_time
from src.scrapers.floorsheet_scraper import get_floorsheet
from src.scrapers.symbols import get_all_listed_symbols

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROGRESS_LOG_INTERVAL = 20


def _snapshot_time_for_trade_date(trade_date, fallback: datetime) -> datetime:
    if trade_date is None:
        return fallback
    return datetime.combine(trade_date, SESSION_END, tzinfo=NPT_OFFSET)


def _insert_floorsheet_rows(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(IntradayFloorsheet).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=["symbol", "contract_no"])
    stmt = stmt.returning(IntradayFloorsheet.id)
    with get_session() as session:
        result = session.execute(stmt)
        return len(result.fetchall())


def run_daily_floorsheet_backfill(symbols: list[str] | None = None) -> dict[str, Any]:
    if not is_market_open_today():
        logger.info("Not a trading day, skipping daily floorsheet backfill")
        return {"skipped": True, "reason": "not a trading day"}

    if symbols is None:
        symbols = get_all_listed_symbols()
    logger.info("Backfilling daily floorsheet for %d symbols", len(symbols))

    start_time = time.perf_counter()
    capture_time = _current_npt_time()

    symbols_processed = 0
    rows_fetched_total = 0
    rows_inserted_total = 0
    failures = 0

    for symbol in symbols:
        symbols_processed += 1
        try:
            floorsheet_rows = get_floorsheet(symbol)
            rows_fetched_total += len(floorsheet_rows)

            db_rows = [
                {
                    "symbol": symbol,
                    "contract_no": row["contract_no"],
                    "snapshot_time": _snapshot_time_for_trade_date(row.get("trade_date"), capture_time),
                    "buyer_broker_id": row["buyer_broker_id"],
                    "seller_broker_id": row["seller_broker_id"],
                    "contract_quantity": int(row["quantity"]) if row["quantity"] is not None else None,
                    "contract_rate": row["rate"],
                    "contract_amount": row["amount"],
                }
                for row in floorsheet_rows
            ]
            rows_inserted_total += _insert_floorsheet_rows(db_rows)
        except Exception:
            logger.exception("Failed to backfill floorsheet for symbol %s", symbol)
            failures += 1

        if symbols_processed % PROGRESS_LOG_INTERVAL == 0:
            logger.info(
                "Progress: %d/%d symbols done, rows_fetched=%d rows_inserted=%d failures=%d",
                symbols_processed,
                len(symbols),
                rows_fetched_total,
                rows_inserted_total,
                failures,
            )

    elapsed_seconds = time.perf_counter() - start_time
    summary = {
        "skipped": False,
        "symbols_processed": symbols_processed,
        "rows_fetched": rows_fetched_total,
        "rows_inserted": rows_inserted_total,
        "failures": failures,
        "execution_time_seconds": round(elapsed_seconds, 2),
    }
    logger.info("Daily floorsheet backfill summary: %s", summary)
    return summary


if __name__ == "__main__":
    run_daily_floorsheet_backfill()
