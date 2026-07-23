from __future__ import annotations

import logging
from typing import Any

from bs4 import BeautifulSoup

from src.scrapers.http_utils import fetch

logger = logging.getLogger(__name__)

LATEST_MARKET_URL = "https://merolagani.com/LatestMarket.aspx"


def _parse_number(text: str) -> float | None:
    cleaned = text.replace(",", "").strip()
    if not cleaned or cleaned == "-":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def scrape_today_price() -> list[dict[str, Any]]:
    response = fetch(LATEST_MARKET_URL)
    soup = BeautifulSoup(response.text, "html.parser")
    table = soup.find("table", class_="live-trading")
    if table is None:
        logger.error("merolagani: could not locate table.live-trading in page")
        return []

    tbody = table.find("tbody")
    rows = tbody.find_all("tr") if tbody else []
    results: list[dict[str, Any]] = []

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 7:
            logger.warning("merolagani: skipping malformed row with %d cells", len(cells))
            continue

        symbol_link = cells[0].find("a")
        symbol = symbol_link.get_text(strip=True) if symbol_link else cells[0].get_text(strip=True)
        if not symbol:
            logger.warning("merolagani: skipping row with no symbol")
            continue

        try:
            results.append(
                {
                    "symbol": symbol,
                    "lastTradedPrice": _parse_number(cells[1].get_text()),
                    "percentChange": _parse_number(cells[2].get_text()),
                    "highPrice": _parse_number(cells[3].get_text()),
                    "lowPrice": _parse_number(cells[4].get_text()),
                    "openPrice": _parse_number(cells[5].get_text()),
                    "totalTradedQuantity": _parse_number(cells[6].get_text()),
                }
            )
        except Exception:
            logger.exception("merolagani: failed to parse row for symbol %s", symbol)
            continue

    logger.info("merolagani: parsed %d rows", len(results))
    return results


def get_symbols() -> list[str]:
    rows = scrape_today_price()
    return sorted({row["symbol"] for row in rows if row.get("symbol")})
