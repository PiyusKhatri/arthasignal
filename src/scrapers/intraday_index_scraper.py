from __future__ import annotations

from typing import Any

from src.scrapers import nepse_api, sharesansar_scraper


def get_intraday_index_snapshot() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for row in nepse_api.get_nepse_index():
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
