from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

import requests

from src.scrapers.http_utils import fetch
from src.scrapers.sharesansar_scraper import _parse_number

PROMOTER_LOCKIN_URL = "https://www.sharesansar.com/promoter-lockin"
PAGE_SIZE = 50

TOKEN_PATTERN = re.compile(r'name="_token" content="([^"]+)"')
LINK_TEXT_PATTERN = re.compile(r">([^<]*)<")

LOCK_STATUS_BY_TYPE = {1: "locked", 0: "unlocked"}


def _get_promoter_lockin_session() -> tuple[requests.Session, str]:
    session = requests.Session()
    response = fetch(PROMOTER_LOCKIN_URL, session=session)
    token_match = TOKEN_PATTERN.search(response.text)
    if not token_match:
        raise ValueError("Could not locate CSRF token on promoter-lockin page")
    return session, token_match.group(1)


def _extract_link_text(raw: str | None) -> str | None:
    if not raw:
        return None
    match = LINK_TEXT_PATTERN.search(raw)
    return match.group(1).strip() if match else raw.strip()


def _parse_percent(raw: str | None) -> float | None:
    if not raw:
        return None
    cleaned = raw.strip().lstrip("(").rstrip(")").rstrip("%").strip()
    return _parse_number(cleaned)


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _fetch_rows(session: requests.Session, token: str, type_value: int) -> list[dict[str, Any]]:
    headers = {
        "X-CSRF-Token": token,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": PROMOTER_LOCKIN_URL,
    }

    rows: list[dict[str, Any]] = []
    start = 0

    while True:
        params = {"draw": 1, "start": start, "length": PAGE_SIZE, "type": type_value, "sector": ""}
        response = fetch(PROMOTER_LOCKIN_URL, session=session, params=params, headers=headers)
        body = response.json()
        page_rows = body.get("data", [])
        if not page_rows:
            break

        for raw_row in page_rows:
            symbol = _extract_link_text(raw_row.get("symbol"))
            if not symbol:
                continue

            rows.append(
                {
                    "symbol": symbol,
                    "total_shares": _parse_number(raw_row.get("shares")),
                    "promoter_shares": _parse_number(raw_row.get("prom_share")),
                    "promoter_pct": _parse_percent(raw_row.get("prom_share_per")),
                    "public_shares": _parse_number(raw_row.get("public_share")),
                    "public_pct": _parse_percent(raw_row.get("public_share_per")),
                    "lock_status": LOCK_STATUS_BY_TYPE[type_value],
                    "prom_lock_date": _parse_date(raw_row.get("prom_lock_date")),
                    "mf_lock_date": _parse_date(raw_row.get("mf_lock_date")),
                    "reported_date": _parse_date(raw_row.get("date")),
                }
            )

        records_total = body.get("recordsTotal", 0)
        start += PAGE_SIZE
        if start >= records_total:
            break

    return rows


def get_promoter_holding() -> list[dict[str, Any]]:
    session, token = _get_promoter_lockin_session()
    rows = _fetch_rows(session, token, 1)
    rows += _fetch_rows(session, token, 0)
    return rows
