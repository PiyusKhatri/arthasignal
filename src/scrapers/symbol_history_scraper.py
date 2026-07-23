from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

import requests

from src.scrapers.http_utils import fetch

logger = logging.getLogger(__name__)

MERGED_COMPANIES_URL = "https://www.sharesansar.com/merged-companies"
MERGER_ACQUISITION_URL = "https://www.sharesansar.com/merger-acquisition"
PAGE_SIZE = 50

HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
SYMBOL_FROM_LINK_PATTERN = re.compile(r">([A-Za-z0-9]+)<")
NON_ALNUM_PATTERN = re.compile(r"[^a-z0-9]+")
COMPANY_SUFFIX_PATTERN = re.compile(r"\b(limited|ltd\.?|pvt\.?)\b")
WHITESPACE_PATTERN = re.compile(r"\s+")
PROMOTER_SUFFIXES = ("PO", "P")


def _normalize_text(raw: str | None) -> str:
    text = HTML_TAG_PATTERN.sub(" ", raw or "")
    text = text.replace("&amp;", " and ").replace("&nbsp;", " ")
    text = text.lower()
    text = COMPANY_SUFFIX_PATTERN.sub("", text)
    text = NON_ALNUM_PATTERN.sub(" ", text)
    return WHITESPACE_PATTERN.sub(" ", text).strip()


def _extract_symbol(html_link: str) -> str | None:
    match = SYMBOL_FROM_LINK_PATTERN.search(html_link or "")
    return match.group(1) if match else None


def _parse_date(raw: str | None):
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _fetch_paginated(url: str, extra_params: dict[str, Any]) -> list[dict[str, Any]]:
    session = requests.Session()
    fetch(url, session=session)

    headers = {"X-Requested-With": "XMLHttpRequest", "Referer": url}
    rows: list[dict[str, Any]] = []
    start = 0

    while True:
        params = {"draw": 1, "start": start, "length": PAGE_SIZE, **extra_params}
        response = fetch(url, session=session, params=params, headers=headers)
        body = response.json()
        page_rows = body.get("data", [])
        rows.extend(page_rows)

        records_total = body.get("recordsTotal", 0)
        start += PAGE_SIZE
        if start >= records_total or len(page_rows) < PAGE_SIZE:
            break

    return rows


def get_merged_companies() -> list[dict[str, Any]]:
    rows = _fetch_paginated(MERGED_COMPANIES_URL, {})
    logger.info("sharesansar: fetched %d merged-companies records", len(rows))
    return rows


def get_merger_acquisition_records() -> list[dict[str, Any]]:
    rows = _fetch_paginated(MERGER_ACQUISITION_URL, {"type": 1})
    logger.info("sharesansar: fetched %d merger-acquisition records", len(rows))
    return rows


def get_symbol_history() -> list[dict[str, Any]]:
    merged_companies = get_merged_companies()
    merger_acquisition_records = get_merger_acquisition_records()

    records: list[dict[str, Any]] = []

    for merged_row in merged_companies:
        old_symbol = _extract_symbol(merged_row.get("symbol", ""))
        old_name = HTML_TAG_PATTERN.sub("", merged_row.get("companyname", ""))
        old_norm = _normalize_text(old_name)

        if not old_symbol or not old_norm:
            logger.warning("sharesansar: skipping merged-companies row with no usable symbol/name: %r", merged_row)
            continue

        candidates = []
        for ma_row in merger_acquisition_records:
            new_symbol = ma_row.get("company", {}).get("symbol")
            if not new_symbol or new_symbol == old_symbol:
                continue
            if old_norm in _normalize_text(ma_row.get("companies", "")):
                candidates.append((new_symbol, ma_row))

        distinct_symbols = {symbol for symbol, _ in candidates}
        if len(distinct_symbols) > 1:
            non_promoter_symbols = {
                symbol for symbol in distinct_symbols if not symbol.upper().endswith(PROMOTER_SUFFIXES)
            }
            if len(non_promoter_symbols) == 1:
                distinct_symbols = non_promoter_symbols
                candidates = [(symbol, row) for symbol, row in candidates if symbol in distinct_symbols]

        if len(distinct_symbols) == 0:
            logger.info("%s: no matching merger-acquisition record found, skipping", old_symbol)
            continue
        if len(distinct_symbols) > 1:
            logger.warning("%s: ambiguous merger match, candidates=%s, skipping", old_symbol, sorted(distinct_symbols))
            continue

        new_symbol, ma_row = candidates[0]
        effective_date = (
            _parse_date(ma_row.get("final_date"))
            or _parse_date(ma_row.get("transaction_date"))
            or _parse_date(ma_row.get("mou_date"))
        )
        if effective_date is None:
            logger.warning("%s: matched to %s but no usable date found, skipping", old_symbol, new_symbol)
            continue

        companies_text = HTML_TAG_PATTERN.sub("", ma_row.get("companies", "")).strip()
        swap_ratio = ma_row.get("swap_ratio")
        notes = companies_text
        if swap_ratio:
            notes = f"{companies_text} | swap ratio {swap_ratio}"

        records.append(
            {
                "old_symbol": old_symbol,
                "new_symbol": new_symbol,
                "event_type": "merger",
                "effective_date": effective_date,
                "notes": notes[:500] if notes else None,
            }
        )

    logger.info("sharesansar: resolved %d symbol history records out of %d merged companies", len(records), len(merged_companies))
    return records
