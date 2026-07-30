from __future__ import annotations

import logging
import time
from typing import Any

from src.pipeline.db_writers import insert_new_gdp_nepse, insert_new_interest_rates, insert_new_remittance
from src.scrapers.macro_scraper import get_gdp_nepse, get_interest_rates, get_remittance

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_macro_data_backfill() -> dict[str, Any]:
    start_time = time.perf_counter()
    failures = 0

    try:
        interest_rate_rows = get_interest_rates()
        interest_inserted, interest_skipped = insert_new_interest_rates(interest_rate_rows)
    except Exception:
        logger.exception("Failed to backfill interest rates")
        interest_rate_rows, interest_inserted, interest_skipped = [], 0, 0
        failures += 1

    try:
        remittance_rows = get_remittance()
        remittance_inserted, remittance_skipped = insert_new_remittance(remittance_rows)
    except Exception:
        logger.exception("Failed to backfill remittance")
        remittance_rows, remittance_inserted, remittance_skipped = [], 0, 0
        failures += 1

    try:
        gdp_rows = get_gdp_nepse()
        gdp_inserted, gdp_skipped = insert_new_gdp_nepse(gdp_rows)
    except Exception:
        logger.exception("Failed to backfill GDP data")
        gdp_rows, gdp_inserted, gdp_skipped = [], 0, 0
        failures += 1

    elapsed_seconds = time.perf_counter() - start_time

    summary = {
        "interest_rates_fetched": len(interest_rate_rows),
        "interest_rates_inserted": interest_inserted,
        "interest_rates_duplicates_skipped": interest_skipped,
        "remittance_fetched": len(remittance_rows),
        "remittance_inserted": remittance_inserted,
        "remittance_duplicates_skipped": remittance_skipped,
        "gdp_fetched": len(gdp_rows),
        "gdp_inserted": gdp_inserted,
        "gdp_duplicates_skipped": gdp_skipped,
        "failures": failures,
        "execution_time_seconds": round(elapsed_seconds, 2),
    }

    logger.info("Macro data backfill summary: %s", summary)
    return summary


if __name__ == "__main__":
    run_macro_data_backfill()
