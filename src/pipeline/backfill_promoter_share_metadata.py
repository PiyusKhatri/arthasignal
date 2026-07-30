from __future__ import annotations

import logging

from sqlalchemy import update

from src.database.connection import get_session
from src.database.models import Company

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROMOTER_SHARE_SECTOR = "Promoter Share"

PROMOTER_SHARE_NAMES = {
    "GBIMEP": "Global IME Bank Limited Promoter Share",
    "GRDBLP": "Green Development Bank Ltd. Promoter Share",
    "HEIP": "Himalayan Everest Insurance Limited Promoter Share",
    "HIDCLP": "Hydroelectricity Investment and Development Company Limited Promoter Share",
    "LSLPO": "Laxmi Sunrise Bank Limited Promoter Share",
    "MSLBP": "Mahuli Laghubitta Bittiya Sanstha Limited Promoter Share",
    "NIMBPO": "Nepal Investment Mega Bank Ltd. Promoter Share",
    "PCBLP": "Prime Commercial Bank Limited Promoter Share",
    "RBCLPO": "Rastriya Beema Company Limited Promoter Share",
    "SLBBLP": "Swarojgar Laghubitta Bittiya Sanstha Limited Promoter Share",
}


def backfill_promoter_share_metadata() -> int:
    updated = 0
    with get_session() as session:
        for symbol, company_name in PROMOTER_SHARE_NAMES.items():
            result = session.execute(
                update(Company)
                .where(Company.symbol == symbol)
                .values(company_name=company_name, sector=PROMOTER_SHARE_SECTOR)
            )
            updated += result.rowcount

    logger.info("Updated %d promoter share companies", updated)
    return updated


if __name__ == "__main__":
    backfill_promoter_share_metadata()
