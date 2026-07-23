from __future__ import annotations

import logging

from nepse_scraper import Nepse_scraper

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    client = Nepse_scraper()

    today_price = client.get_today_price()
    logger.info("get_today_price returned %d rows", len(today_price))
    for row in today_price[:5]:
        print(row)

    market_summary = client.get_market_summary()
    logger.info("get_market_summary returned %d entries", len(market_summary))
    for row in market_summary:
        print(row)

    sector_summary = client.get_sectorwise_summary()
    logger.info("get_sectorwise_summary returned %d entries", len(sector_summary))
    for row in sector_summary[:5]:
        print(row)


if __name__ == "__main__":
    main()
