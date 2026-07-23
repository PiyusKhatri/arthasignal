from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any

import requests

from src.scrapers.http_utils import post
from src.scrapers.sharesansar_scraper import _get_company_session

logger = logging.getLogger(__name__)

DIVIDEND_URL = "https://www.sharesansar.com/company-dividend"
RIGHTSHARE_URL = "https://www.sharesansar.com/company-rightshare"
PAGE_SIZE = 50

DATE_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    match = DATE_PATTERN.search(raw)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_positive_percent(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if value > 0 else None


def _derive_fiscal_year_label(action_date: date) -> str:
    start_year = action_date.year if action_date.month >= 7 else action_date.year - 1
    return f"{start_year}/{start_year + 1}"


def _fetch_paginated(
    url: str,
    session: requests.Session,
    company_id: str,
    headers: dict[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        data = {"company": company_id, "draw": 1, "start": start, "length": PAGE_SIZE}
        response = post(url, session=session, data=data, headers=headers)
        body = response.json()
        page_rows = body.get("data", [])
        rows.extend(page_rows)
        records_total = body.get("recordsTotal", 0)
        start += PAGE_SIZE
        if start >= records_total or len(page_rows) < PAGE_SIZE:
            break
    return rows


def get_corporate_actions(symbol: str) -> list[dict[str, Any]]:
    session, token, company_id = _get_company_session(symbol)
    headers = {
        "X-CSRF-Token": token,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://www.sharesansar.com/company/{symbol.lower()}",
    }

    actions: list[dict[str, Any]] = []

    dividend_rows = _fetch_paginated(DIVIDEND_URL, session, company_id, headers)
    for row in dividend_rows:
        action_date = _parse_date(row.get("bookclose_date"))
        if action_date is None:
            logger.warning("%s: skipping dividend row with no book closure date: %r", symbol, row)
            continue
        fiscal_year = row.get("year") or _derive_fiscal_year_label(action_date)

        bonus_percent = _parse_positive_percent(row.get("bonus_share"))
        if bonus_percent is not None:
            actions.append(
                {
                    "symbol": symbol,
                    "action_date": action_date,
                    "action_type": "bonus",
                    "ratio_or_amount": bonus_percent,
                    "fiscal_year": fiscal_year,
                }
            )

        cash_percent = _parse_positive_percent(row.get("cash_dividend"))
        if cash_percent is not None:
            actions.append(
                {
                    "symbol": symbol,
                    "action_date": action_date,
                    "action_type": "dividend",
                    "ratio_or_amount": cash_percent,
                    "fiscal_year": fiscal_year,
                }
            )

    rightshare_rows = _fetch_paginated(RIGHTSHARE_URL, session, company_id, headers)
    for row in rightshare_rows:
        action_date = _parse_date(row.get("final_date"))
        if action_date is None:
            logger.warning("%s: skipping right share row with no book closure date: %r", symbol, row)
            continue

        ratio_raw = (row.get("ratio_value") or "").strip()
        parts = ratio_raw.split(":")
        if len(parts) != 2:
            logger.warning("%s: skipping right share row with unparseable ratio %r", symbol, ratio_raw)
            continue
        try:
            old_units = float(parts[0])
            new_units = float(parts[1])
        except ValueError:
            logger.warning("%s: skipping right share row with non-numeric ratio %r", symbol, ratio_raw)
            continue
        if old_units <= 0:
            continue

        actions.append(
            {
                "symbol": symbol,
                "action_date": action_date,
                "action_type": "right",
                "ratio_or_amount": (new_units / old_units) * 100,
                "fiscal_year": _derive_fiscal_year_label(action_date),
            }
        )

    logger.info("%s: found %d corporate action records", symbol, len(actions))
    return actions
