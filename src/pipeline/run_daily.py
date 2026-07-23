from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Any

from src.pipeline.adjustment_ops import reapply_adjustment_for_symbol, symbols_with_corporate_actions
from src.pipeline.data_quality import check_daily_pipeline_health
from src.pipeline.db_writers import build_company_records, insert_new_daily_prices, upsert_companies
from src.scrapers.market_data import get_today_price_with_fallback
from src.scrapers.symbols import get_all_listed_symbols

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _normalize_price_row(row: dict[str, Any], fallback_date: date) -> dict[str, Any]:
    symbol = row.get("symbol")
    if not symbol:
        raise ValueError("missing symbol")

    business_date_raw = row.get("businessDate")
    if business_date_raw:
        row_date = datetime.strptime(business_date_raw, "%Y-%m-%d").date()
    else:
        row_date = fallback_date

    open_price = row.get("openPrice")
    high_price = row.get("highPrice")
    low_price = row.get("lowPrice")
    close_price = row.get("closePrice")
    if close_price is None:
        close_price = row.get("lastTradedPrice")
    if close_price is None:
        close_price = row.get("lastUpdatedPrice")
    volume = row.get("totalTradedQuantity")
    turnover = row.get("totalTradedValue")

    if open_price is None or high_price is None or low_price is None or close_price is None or volume is None:
        raise ValueError(f"missing required price field for {symbol}")

    return {
        "symbol": symbol,
        "date": row_date,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "volume": int(volume),
        "turnover": turnover if turnover is not None else 0.0,
    }


def _normalize_price_rows(raw_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    fallback_date = date.today()
    normalized = []
    failures = 0
    for row in raw_rows:
        try:
            normalized.append(_normalize_price_row(row, fallback_date))
        except Exception as exc:
            failures += 1
            logger.warning(
                "Skipping unparseable price row for symbol %s: %s | raw row: %r",
                row.get("symbol"),
                exc,
                row,
            )
    return normalized, failures


def run_daily_pipeline() -> dict[str, Any]:
    start_time = time.perf_counter()

    symbols = get_all_listed_symbols()
    logger.info("Retrieved %d listed symbols", len(symbols))

    raw_price_rows = get_today_price_with_fallback()
    logger.info("Retrieved %d raw price rows", len(raw_price_rows))

    normalized_rows, price_row_parse_failures = _normalize_price_rows(raw_price_rows)

    all_symbols = sorted(set(symbols) | {row["symbol"] for row in normalized_rows})
    company_records = build_company_records(all_symbols)
    company_upsert_failed = False
    try:
        companies_processed = upsert_companies(company_records)
    except Exception:
        logger.exception("Failed to upsert companies")
        companies_processed = 0
        company_upsert_failed = True

    price_insert_failed = False
    try:
        new_rows_inserted, duplicates_skipped = insert_new_daily_prices(normalized_rows)
    except Exception:
        logger.exception("Failed to insert daily prices")
        new_rows_inserted, duplicates_skipped = 0, 0
        price_insert_failed = True

    try:
        data_quality_summary = check_daily_pipeline_health()
        data_quality_flags = data_quality_summary["checks_flagged"]
    except Exception:
        logger.exception("Failed to run data quality health check")
        data_quality_flags = None

    corporate_action_lookup_failed = False
    try:
        symbols_needing_adjustment = symbols_with_corporate_actions()
    except Exception:
        logger.exception("Failed to determine symbols with corporate actions")
        symbols_needing_adjustment = []
        corporate_action_lookup_failed = True

    adjustment_failures = 0
    for symbol in symbols_needing_adjustment:
        try:
            reapply_adjustment_for_symbol(symbol)
        except Exception:
            logger.exception("Failed to reapply adjustment for symbol %s", symbol)
            adjustment_failures += 1

    elapsed_seconds = time.perf_counter() - start_time

    total_failures = (
        price_row_parse_failures
        + int(company_upsert_failed)
        + int(price_insert_failed)
        + int(corporate_action_lookup_failed)
        + adjustment_failures
    )

    summary = {
        "companies_processed": companies_processed,
        "new_price_rows_inserted": new_rows_inserted,
        "duplicates_skipped": duplicates_skipped,
        "data_quality_flags": data_quality_flags,
        "raw_price_rows_received": len(raw_price_rows),
        "price_row_parse_failures": price_row_parse_failures,
        "company_upsert_failed": company_upsert_failed,
        "price_insert_failed": price_insert_failed,
        "corporate_action_lookup_failed": corporate_action_lookup_failed,
        "adjustment_symbols_processed": len(symbols_needing_adjustment),
        "adjustment_failures": adjustment_failures,
        "failures": total_failures,
        "execution_time_seconds": round(elapsed_seconds, 2),
    }

    logger.info(
        "Daily pipeline summary: companies_processed=%d new_price_rows_inserted=%d duplicates_skipped=%d "
        "data_quality_flags=%s price_row_parse_failures=%d/%d company_upsert_failed=%s price_insert_failed=%s "
        "corporate_action_lookup_failed=%s adjustment_failures=%d/%d total_failures=%d execution_time_seconds=%.2f",
        summary["companies_processed"],
        summary["new_price_rows_inserted"],
        summary["duplicates_skipped"],
        summary["data_quality_flags"],
        summary["price_row_parse_failures"],
        summary["raw_price_rows_received"],
        summary["company_upsert_failed"],
        summary["price_insert_failed"],
        summary["corporate_action_lookup_failed"],
        summary["adjustment_failures"],
        summary["adjustment_symbols_processed"],
        summary["failures"],
        summary["execution_time_seconds"],
    )

    return summary


if __name__ == "__main__":
    run_daily_pipeline()
