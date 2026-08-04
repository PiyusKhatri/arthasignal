from __future__ import annotations

import logging
from typing import Any

from src.scrapers import nepse_api, sharesansar_scraper

logger = logging.getLogger(__name__)


def get_intraday_index_snapshot() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    try:
        nepse_index_rows = nepse_api.get_nepse_index()
    except Exception:
        logger.exception(
            "nepse_api.get_nepse_index() failed - nepalstock.com is known to block non-Nepal IPs (e.g. "
            "GitHub Actions runners), so the primary NEPSE Index value will be missing from this snapshot "
            "round; continuing with sharesansar sub-indices only rather than failing the whole pipeline run"
        )
        nepse_index_rows = []

    for row in nepse_index_rows:
        index_name = row.get("index")
        if not index_name:
            continue
        results.append(
            {
                "index_name": index_name,
                "current_value": row.get("currentValue"),
                "percent_change": row.get("perChange"),
                "points_change": row.get("change"),
            }
        )

    for row in sharesansar_scraper.scrape_sub_indices():
        index_name = row.get("indexName")
        if not index_name:
            continue
        results.append(
            {
                "index_name": index_name,
                "current_value": row.get("close"),
                "percent_change": row.get("percentChange"),
                "points_change": row.get("pointChange"),
            }
        )

    return results
