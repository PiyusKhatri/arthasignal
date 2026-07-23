from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any

import requests
from bs4 import BeautifulSoup

from src.scrapers.http_utils import fetch, post

logger = logging.getLogger(__name__)

TODAY_PRICE_URL = "https://www.sharesansar.com/today-share-price"
COMPANY_PAGE_URL_TEMPLATE = "https://www.sharesansar.com/company/{slug}"
PRICE_HISTORY_URL = "https://www.sharesansar.com/company-price-history"
PRICE_HISTORY_PAGE_SIZE = 50

TOKEN_PATTERN = re.compile(r'name="_token" content="([^"]+)"')
COMPANY_ID_PATTERN = re.compile(r'id="companyid" style="display: none;">(\d+)</div>')


def _parse_number(text: str) -> float | None:
    cleaned = text.replace(",", "").strip()
    if not cleaned or cleaned == "-":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def scrape_today_price() -> list[dict[str, Any]]:
    response = fetch(TODAY_PRICE_URL)
    soup = BeautifulSoup(response.text, "html.parser")
    table = soup.find("table", id="headFixed")
    if table is None:
        logger.error("sharesansar: could not locate table#headFixed in page")
        return []

    tbody = table.find("tbody")
    rows = tbody.find_all("tr") if tbody else []
    results: list[dict[str, Any]] = []

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 14:
            logger.warning("sharesansar: skipping malformed row with %d cells", len(cells))
            continue

        symbol_link = cells[1].find("a")
        symbol = symbol_link.get_text(strip=True) if symbol_link else cells[1].get_text(strip=True)
        if not symbol:
            logger.warning("sharesansar: skipping row with no symbol")
            continue

        try:
            results.append(
                {
                    "symbol": symbol,
                    "openPrice": _parse_number(cells[3].get_text()),
                    "highPrice": _parse_number(cells[4].get_text()),
                    "lowPrice": _parse_number(cells[5].get_text()),
                    "closePrice": _parse_number(cells[6].get_text()),
                    "lastTradedPrice": _parse_number(cells[7].get_text()),
                    "totalTradedQuantity": _parse_number(cells[11].get_text()),
                    "previousDayClosePrice": _parse_number(cells[12].get_text()),
                    "totalTradedValue": _parse_number(cells[13].get_text()),
                }
            )
        except Exception:
            logger.exception("sharesansar: failed to parse row for symbol %s", symbol)
            continue

    logger.info("sharesansar: parsed %d rows", len(results))
    return results


def get_symbols() -> list[str]:
    rows = scrape_today_price()
    return sorted({row["symbol"] for row in rows if row.get("symbol")})


def _get_company_session(symbol: str) -> tuple[requests.Session, str, str]:
    session = requests.Session()
    url = COMPANY_PAGE_URL_TEMPLATE.format(slug=symbol.lower())
    response = fetch(url, session=session)
    token_match = TOKEN_PATTERN.search(response.text)
    company_id_match = COMPANY_ID_PATTERN.search(response.text)
    if not token_match or not company_id_match:
        raise ValueError(f"Could not locate CSRF token or company id for {symbol}")
    return session, token_match.group(1), company_id_match.group(1)


def get_price_history(symbol: str, years: int = 5) -> list[dict[str, Any]]:
    session, token, company_id = _get_company_session(symbol)
    cutoff_date = date.today() - timedelta(days=years * 365)

    headers = {
        "X-CSRF-Token": token,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": COMPANY_PAGE_URL_TEMPLATE.format(slug=symbol.lower()),
    }

    rows: list[dict[str, Any]] = []
    start = 0

    while True:
        payload = {
            "company": company_id,
            "draw": 1,
            "start": start,
            "length": PRICE_HISTORY_PAGE_SIZE,
        }
        response = post(PRICE_HISTORY_URL, session=session, data=payload, headers=headers)
        body = response.json()
        page_rows = body.get("data", [])
        if not page_rows:
            break

        reached_cutoff = False
        for raw_row in page_rows:
            try:
                row_date = datetime.strptime(raw_row["published_date"], "%Y-%m-%d").date()
            except (KeyError, ValueError):
                logger.warning("sharesansar: skipping malformed price history row for %s: %r", symbol, raw_row)
                continue

            if row_date < cutoff_date:
                reached_cutoff = True
                break

            rows.append(
                {
                    "symbol": symbol,
                    "date": row_date,
                    "open": _parse_number(raw_row.get("open", "")),
                    "high": _parse_number(raw_row.get("high", "")),
                    "low": _parse_number(raw_row.get("low", "")),
                    "close": _parse_number(raw_row.get("close", "")),
                    "volume": int(_parse_number(raw_row.get("traded_quantity", "")) or 0),
                    "turnover": _parse_number(raw_row.get("traded_amount", "")) or 0.0,
                }
            )

        if reached_cutoff or len(page_rows) < PRICE_HISTORY_PAGE_SIZE:
            break
        start += PRICE_HISTORY_PAGE_SIZE

    logger.info("sharesansar: fetched %d historical rows for %s", len(rows), symbol)
    return rows
