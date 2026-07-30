from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup

from src.scrapers.http_utils import fetch
from src.scrapers.sharesansar_scraper import _parse_number

INTEREST_RATE_URL = "https://www.sharesansar.com/short-term-interest-rates"
REMITTANCE_URL = "https://www.sharesansar.com/remittance"
GDP_URL = "https://www.sharesansar.com/gdp-market-capitalization"

FISCAL_YEAR_VALUES = {
    32: "2082/2083",
    31: "2081/2082",
    30: "2080/2081",
    29: "2079/2080",
    28: "2078/2079",
    27: "2077/2078",
    26: "2076/2077",
    24: "2075/2076",
    5: "2074/2075",
    4: "2073/2074",
}

AJAX_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


def get_interest_rates() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for year_id, fiscal_year in FISCAL_YEAR_VALUES.items():
        response = fetch(INTEREST_RATE_URL, params={"year": year_id}, headers=AJAX_HEADERS)
        soup = BeautifulSoup(response.text, "html.parser")

        for tr in soup.find_all("tr")[2:]:
            cells = tr.find_all("td")
            if len(cells) < 5:
                continue

            other_rate = _parse_number(cells[4].get_text(strip=True))
            if other_rate == 0:
                other_rate = None

            rows.append(
                {
                    "fiscal_year": fiscal_year,
                    "month": cells[1].get_text(strip=True),
                    "treasury_bill_rate": _parse_number(cells[2].get_text(strip=True)),
                    "interbank_commercial_rate": _parse_number(cells[3].get_text(strip=True)),
                    "interbank_other_rate": other_rate,
                }
            )

    return rows


def get_remittance() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for year_id, fiscal_year in FISCAL_YEAR_VALUES.items():
        response = fetch(REMITTANCE_URL, params={"year": year_id}, headers=AJAX_HEADERS)
        soup = BeautifulSoup(response.text, "html.parser")

        for tr in soup.find_all("tr")[1:]:
            cells = tr.find_all("td")
            if len(cells) < 4:
                continue

            rows.append(
                {
                    "fiscal_year": fiscal_year,
                    "month": cells[1].get_text(strip=True),
                    "amount_billions": _parse_number(cells[2].get_text(strip=True)),
                    "growth_pct": _parse_number(cells[3].get_text(strip=True)),
                }
            )

    return rows


def get_gdp_nepse() -> list[dict[str, Any]]:
    response = fetch(GDP_URL)
    soup = BeautifulSoup(response.text, "html.parser")
    table = soup.find("table")
    if table is None:
        return []

    rows: list[dict[str, Any]] = []
    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all("td")
        if len(cells) < 8:
            continue

        growth_rate = _parse_number(cells[2].get_text(strip=True))
        if growth_rate == 0:
            growth_rate = None

        rows.append(
            {
                "fiscal_year": cells[1].get_text(strip=True),
                "gdp_growth_rate": growth_rate,
                "market_cap": _parse_number(cells[3].get_text(strip=True)),
                "market_cap_gdp_ratio": _parse_number(cells[4].get_text(strip=True)),
                "nepse_index": _parse_number(cells[7].get_text(strip=True)),
            }
        )

    return rows
