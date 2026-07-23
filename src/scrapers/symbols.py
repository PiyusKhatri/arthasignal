from __future__ import annotations

import logging
from typing import Iterable

from src.scrapers import merolagani_scraper, nepse_api, sharesansar_scraper

logger = logging.getLogger(__name__)

MIN_EXPECTED_SYMBOLS = 50


def get_all_listed_symbols(
    instrument_types: Iterable[str] = ("Equity",),
    statuses: Iterable[str] = ("A",),
) -> list[str]:
    try:
        symbols = nepse_api.get_filtered_symbols(instrument_types, statuses)
        if len(symbols) >= MIN_EXPECTED_SYMBOLS:
            logger.info("Symbol list sourced from nepse-scraper: %d symbols", len(symbols))
            return sorted(set(symbols))
        logger.warning("nepse-scraper returned only %d symbols, falling back", len(symbols))
    except Exception:
        logger.exception("nepse-scraper failed to provide symbol list, falling back")

    try:
        symbols = sharesansar_scraper.get_symbols()
        if len(symbols) >= MIN_EXPECTED_SYMBOLS:
            logger.info("Symbol list sourced from sharesansar: %d symbols", len(symbols))
            return symbols
        logger.warning("sharesansar returned only %d symbols, falling back", len(symbols))
    except Exception:
        logger.exception("sharesansar fallback failed, trying next source")

    try:
        symbols = merolagani_scraper.get_symbols()
        if len(symbols) >= MIN_EXPECTED_SYMBOLS:
            logger.info("Symbol list sourced from merolagani: %d symbols", len(symbols))
            return symbols
        logger.warning("merolagani returned only %d symbols", len(symbols))
    except Exception:
        logger.exception("merolagani fallback failed")

    logger.error("All symbol sources failed or returned insufficient data")
    return []
