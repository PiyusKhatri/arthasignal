from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

import requests

from src.scrapers.http_utils import fetch
from src.scrapers.sharesansar_scraper import _parse_number

EXISTING_ISSUES_URL = "https://www.sharesansar.com/existing-issues"
PAGE_SIZE = 50

TOKEN_PATTERN = re.compile(r'name="_token" content="([^"]+)"')
LINK_TEXT_PATTERN = re.compile(r">([^<]*)<")

ISSUE_TYPE_VALUES = {
    1: "IPO",
    2: "FPO",
    3: "Right",
    4: "Mutual Fund",
    5: "IPO Local",
    7: "Debenture",
    8: "IPO Migrant",
    9: "IPO QII",
}


def _get_existing_issues_session() -> tuple[requests.Session, str]:
    session = requests.Session()
    response = fetch(EXISTING_ISSUES_URL, session=session)
    token_match = TOKEN_PATTERN.search(response.text)
    if not token_match:
        raise ValueError("Could not locate CSRF token on existing-issues page")
    return session, token_match.group(1)


def _extract_link_text(raw: str | None) -> str | None:
    if not raw:
        return None
    match = LINK_TEXT_PATTERN.search(raw)
    text = match.group(1).strip() if match else raw.strip()
    return text or None


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _safe_parse_number(raw: Any) -> float | None:
    if raw is None:
        return None
    return _parse_number(str(raw))


def _status_from_source(raw_status: Any) -> str:
    if raw_status is None:
        return "upcoming"
    if raw_status in (-2, -1):
        return "upcoming"
    if raw_status == 0:
        return "open"
    return "closed"


def _fetch_issue_type_rows(session: requests.Session, token: str, type_id: int, issue_type: str) -> list[dict[str, Any]]:
    headers = {
        "X-CSRF-Token": token,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": EXISTING_ISSUES_URL,
    }

    rows: list[dict[str, Any]] = []
    start = 0
    source_updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    while True:
        params = {"draw": 1, "start": start, "length": PAGE_SIZE, "type": type_id}
        response = fetch(EXISTING_ISSUES_URL, session=session, params=params, headers=headers)
        body = response.json()
        page_rows = body.get("data", [])
        if not page_rows:
            break

        for raw_row in page_rows:
            company = raw_row.get("company") or {}
            company_name = _extract_link_text(company.get("companyname"))
            if not company_name:
                continue

            rows.append(
                {
                    "symbol": _extract_link_text(company.get("symbol")),
                    "company_name": company_name,
                    "issue_type": issue_type,
                    "opening_date": _parse_date(raw_row.get("opening_date")),
                    "closing_date": _parse_date(raw_row.get("closing_date")),
                    "price": _safe_parse_number(raw_row.get("issue_price")),
                    "units_offered": _safe_parse_number(raw_row.get("total_units")),
                    "min_application_units": None,
                    "status": _status_from_source(raw_row.get("status")),
                    "result_announced_date": None,
                    "source_updated_at": source_updated_at,
                }
            )

        records_total = body.get("recordsTotal", 0)
        start += PAGE_SIZE
        if start >= records_total:
            break

    return rows


def get_ipo_calendar() -> list[dict[str, Any]]:
    session, token = _get_existing_issues_session()
    rows: list[dict[str, Any]] = []
    for type_id, issue_type in ISSUE_TYPE_VALUES.items():
        rows += _fetch_issue_type_rows(session, token, type_id, issue_type)
    return rows
