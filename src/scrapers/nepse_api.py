from __future__ import annotations

import logging
from typing import Any, Iterable

from nepse_scraper import Nepse_scraper
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_client = Nepse_scraper()


@retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_today_price() -> list[dict[str, Any]]:
    logger.info("Fetching today's price from nepse-scraper")
    return _client.get_today_price()


@retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_market_summary() -> list[dict[str, Any]]:
    logger.info("Fetching market summary from nepse-scraper")
    return _client.get_market_summary()


@retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_sectorwise_summary() -> list[dict[str, Any]]:
    logger.info("Fetching sectorwise summary from nepse-scraper")
    return _client.get_sectorwise_summary()


@retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_all_securities() -> list[dict[str, Any]]:
    logger.info("Fetching full securities list from nepse-scraper")
    return _client.get_all_securities()


@retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def is_market_open() -> bool:
    logger.info("Checking live market open status from nepse-scraper")
    try:
        return _client.is_market_open()
    except Exception as exc:
        response = getattr(exc, "response", None)
        if response is not None:
            logger.error(
                "is_market_open() call failed: status=%s headers=%s body=%s",
                response.status_code,
                dict(response.headers),
                response.text[:2000],
            )
        else:
            logger.error("is_market_open() call failed with no HTTP response attached: %r", exc)
        raise


def get_filtered_symbols(
    instrument_types: Iterable[str] = ("Equity",),
    statuses: Iterable[str] = ("A",),
) -> list[str]:
    instrument_types = set(instrument_types)
    statuses = set(statuses)
    securities = get_all_securities()
    symbols = []
    for security in securities:
        try:
            if security.get("status") in statuses and security.get("instrumentType") in instrument_types and security.get("symbol"):
                symbols.append(security["symbol"])
        except AttributeError:
            logger.warning("Skipping malformed security record: %r", security)
    return symbols
