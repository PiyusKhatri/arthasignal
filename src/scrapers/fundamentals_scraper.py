from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

from bs4 import BeautifulSoup

from src.scrapers.http_utils import fetch

logger = logging.getLogger(__name__)

COMPANY_DETAIL_URL_TEMPLATE = "https://merolagani.com/CompanyDetail.aspx?symbol={symbol}"
VALUE_WITH_FY_PATTERN = re.compile(r"^([\d,]+\.?\d*)\s*(?:\(FY:(\d+-\d+)(?:,\s*Q:(\d+))?\))?")

FIELD_LABELS = {
    "EPS": "eps",
    "P/E Ratio": "pe_ratio",
    "Book Value": "book_value",
    "PBV": "pb_ratio",
    "Market Capitalization": "market_capitalization",
}


def _parse_number(raw: str | None) -> float | None:
    if not raw:
        return None
    cleaned = raw.replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_value_with_fy(text: str) -> tuple[str | None, str | None, str | None]:
    match = VALUE_WITH_FY_PATTERN.match(text)
    if not match:
        return None, None, None
    return match.group(1), match.group(2), match.group(3)


def _normalize_fiscal_year(short_fy: str) -> str:
    start, end = short_fy.split("-")
    return f"2{start}/2{end}"


def _derive_fiscal_year_label(as_of: date) -> str:
    start_year = as_of.year if as_of.month >= 7 else as_of.year - 1
    return f"{start_year}/{start_year + 1}"


def get_fundamentals(symbol: str) -> dict[str, Any] | None:
    url = COMPANY_DETAIL_URL_TEMPLATE.format(symbol=symbol)
    response = fetch(url)
    soup = BeautifulSoup(response.text, "html.parser")

    values: dict[str, float | None] = {}
    fiscal_year: str | None = None
    found_any_field = False

    for th in soup.find_all("th", style=lambda v: v and "200px" in v):
        label = th.get_text(strip=True)
        if label not in FIELD_LABELS:
            continue
        td = th.find_next_sibling("td")
        if td is None:
            continue

        raw_text = td.get_text(" ", strip=True)
        raw_value, raw_fy, _ = _parse_value_with_fy(raw_text)
        values[FIELD_LABELS[label]] = _parse_number(raw_value)
        found_any_field = True

        if raw_fy:
            fiscal_year = _normalize_fiscal_year(raw_fy)

    if not found_any_field:
        logger.warning("%s: no fundamentals fields found on merolagani company page", symbol)
        return None

    reported_date = date.today()
    if fiscal_year is None:
        fiscal_year = _derive_fiscal_year_label(reported_date)

    record = {
        "symbol": symbol,
        "fiscal_year": fiscal_year,
        "eps": values.get("eps"),
        "pe_ratio": values.get("pe_ratio"),
        "pb_ratio": values.get("pb_ratio"),
        "book_value": values.get("book_value"),
        "market_capitalization": values.get("market_capitalization"),
        "reported_date": reported_date,
    }

    logger.info("%s: fundamentals snapshot retrieved", symbol)
    return record
