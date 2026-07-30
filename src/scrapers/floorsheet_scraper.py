from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import requests

from src.scrapers.http_utils import fetch
from src.scrapers.sharesansar_scraper import _parse_number

FLOORSHEET_URL = "https://www.sharesansar.com/floorsheet"
FLOORSHEET_PAGE_SIZE = 500

TOKEN_PATTERN = re.compile(r'name="_token" content="([^"]+)"')


def _get_floorsheet_session() -> tuple[requests.Session, str]:
    session = requests.Session()
    response = fetch(FLOORSHEET_URL, session=session)
    token_match = TOKEN_PATTERN.search(response.text)
    if not token_match:
        raise ValueError("Could not locate CSRF token on floorsheet page")
    return session, token_match.group(1)


def get_floorsheet(symbol: str) -> list[dict[str, Any]]:
    session, token = _get_floorsheet_session()
    headers = {
        "X-CSRF-Token": token,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": FLOORSHEET_URL,
    }

    rows: list[dict[str, Any]] = []
    start = 0

    while True:
        params = {
            "draw": 1,
            "start": start,
            "length": FLOORSHEET_PAGE_SIZE,
            "company": symbol,
            "buyer": "",
            "seller": "",
        }
        response = fetch(FLOORSHEET_URL, session=session, params=params, headers=headers)
        body = response.json()
        page_rows = body.get("data", [])
        if not page_rows:
            break

        for raw_row in page_rows:
            trade_date_raw = raw_row.get("date_")
            trade_date = None
            if trade_date_raw:
                try:
                    trade_date = datetime.strptime(trade_date_raw, "%Y-%m-%d").date()
                except ValueError:
                    trade_date = None

            rows.append(
                {
                    "symbol": symbol,
                    "contract_no": raw_row.get("contract_no"),
                    "buyer_broker_id": raw_row.get("buyer"),
                    "seller_broker_id": raw_row.get("seller"),
                    "quantity": _parse_number(str(raw_row.get("quantity", ""))),
                    "rate": _parse_number(str(raw_row.get("rate", ""))),
                    "amount": _parse_number(str(raw_row.get("amount", ""))),
                    "trade_date": trade_date,
                }
            )

        records_total = body.get("recordsTotal", 0)
        start += FLOORSHEET_PAGE_SIZE
        if start >= records_total or len(page_rows) < FLOORSHEET_PAGE_SIZE:
            break

    return rows
