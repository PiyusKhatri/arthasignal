from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any

import requests

from src.scrapers.http_utils import fetch

logger = logging.getLogger(__name__)

INDEX_HISTORY_URL = "https://www.sharesansar.com/index-history-data"
INDEX_HISTORY_PAGE_SIZE = 50

TOKEN_PATTERN = re.compile(r'name="_token" content="([^"]+)"')

INDEX_NAME_TO_ID = {
    "Banking SubIndex": 1,
    "Development Bank Index": 2,
    "Finance Index": 3,
    "Float Index": 4,
    "Hotels And Tourism": 5,
    "HydroPower Index": 6,
    "Insurance": 7,
    "Investment": 18,
    "Life Insurance": 8,
    "Manufacturing And Processing": 9,
    "Microfinance Index": 10,
    "Mutual Fund": 11,
    "NEPSE Index": 12,
    "Non Life Insurance": 13,
    "Others Index": 14,
    "Sensitive Float Index": 15,
    "Sensitive Index": 16,
    "Trading Index": 17,
}


def _parse_number(text: str) -> float | None:
    cleaned = text.replace(",", "").strip()
    if not cleaned or cleaned == "-":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _get_csrf_session() -> tuple[requests.Session, str]:
    session = requests.Session()
    response = fetch(INDEX_HISTORY_URL, session=session)
    token_match = TOKEN_PATTERN.search(response.text)
    if not token_match:
        raise ValueError("Could not locate CSRF token on index-history-data page")
    return session, token_match.group(1)


def get_index_history(index_name: str, years: int = 5) -> list[dict[str, Any]]:
    index_id = INDEX_NAME_TO_ID.get(index_name)
    if index_id is None:
        raise ValueError(f"Unknown index name: {index_name}")

    session, token = _get_csrf_session()
    end_date = date.today()
    start_date = end_date - timedelta(days=years * 365)

    headers = {
        "X-CSRF-Token": token,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": INDEX_HISTORY_URL,
    }

    rows: list[dict[str, Any]] = []
    offset = 0

    while True:
        params = {
            "index_id": index_id,
            "from": start_date.isoformat(),
            "to": end_date.isoformat(),
            "draw": 1,
            "start": offset,
            "length": INDEX_HISTORY_PAGE_SIZE,
        }
        response = fetch(INDEX_HISTORY_URL, session=session, params=params, headers=headers)
        body = response.json()
        page_rows = body.get("data", [])
        if not page_rows:
            break

        for raw_row in page_rows:
            try:
                row_date = datetime.strptime(raw_row["published_date"], "%Y-%m-%d").date()
            except (KeyError, ValueError):
                logger.warning("index_scraper: skipping malformed row for %s: %r", index_name, raw_row)
                continue

            rows.append(
                {
                    "index_name": index_name,
                    "date": row_date,
                    "open": _parse_number(raw_row.get("open", "")),
                    "high": _parse_number(raw_row.get("high", "")),
                    "low": _parse_number(raw_row.get("low", "")),
                    "close": _parse_number(raw_row.get("current", "")),
                    "points_change": _parse_number(raw_row.get("change_", "")),
                    "percent_change": _parse_number(raw_row.get("per_change", "")),
                }
            )

        records_total = body.get("recordsTotal", 0)
        offset += INDEX_HISTORY_PAGE_SIZE
        if offset >= records_total or len(page_rows) < INDEX_HISTORY_PAGE_SIZE:
            break

    logger.info("index_scraper: fetched %d historical rows for %s", len(rows), index_name)
    return rows
