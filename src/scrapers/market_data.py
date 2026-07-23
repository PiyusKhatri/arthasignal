from __future__ import annotations

import logging
from typing import Any

from src.scrapers import merolagani_scraper, nepse_api, sharesansar_scraper

logger = logging.getLogger(__name__)

MIN_EXPECTED_ROWS = 50


def get_today_price_with_fallback() -> list[dict[str, Any]]:
    try:
        rows = nepse_api.get_today_price()
        if len(rows) >= MIN_EXPECTED_ROWS:
            logger.info("Today's price sourced from nepse-scraper: %d rows", len(rows))
            return rows
        logger.warning("nepse-scraper returned only %d rows, falling back", len(rows))
    except Exception:
        logger.exception("nepse-scraper failed to provide today's price, falling back")

    try:
        rows = sharesansar_scraper.scrape_today_price()
        if len(rows) >= MIN_EXPECTED_ROWS:
            logger.info("Today's price sourced from sharesansar: %d rows", len(rows))
            return rows
        logger.warning("sharesansar returned only %d rows, falling back", len(rows))
    except Exception:
        logger.exception("sharesansar fallback failed, trying next source")

    try:
        rows = merolagani_scraper.scrape_today_price()
        if len(rows) >= MIN_EXPECTED_ROWS:
            logger.info("Today's price sourced from merolagani: %d rows", len(rows))
            return rows
        logger.warning("merolagani returned only %d rows", len(rows))
    except Exception:
        logger.exception("merolagani fallback failed")

    logger.error("All today's-price sources failed or returned insufficient data")
    return []
