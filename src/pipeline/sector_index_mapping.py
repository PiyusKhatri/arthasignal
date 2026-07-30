from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import SectorIndexMapping

SECTOR_TO_INDEX = {
    "Commercial Banks": "Banking SubIndex",
    "Development Banks": "Development Bank Index",
    "Finance": "Finance Index",
    "Hotels And Tourism": "Hotels And Tourism",
    "Hydro Power": "HydroPower Index",
    "Investment": "Investment",
    "Life Insurance": "Life Insurance",
    "Manufacturing And Processing": "Manufacturing And Processing",
    "Microfinance": "Microfinance Index",
    "Non Life Insurance": "Non Life Insurance",
    "Others": "Others Index",
    "Tradings": "Trading Index",
}


def seed_sector_index_mapping() -> int:
    rows = [{"companies_sector": sector, "market_index_name": index_name} for sector, index_name in SECTOR_TO_INDEX.items()]
    stmt = pg_insert(SectorIndexMapping).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["companies_sector"],
        set_={"market_index_name": stmt.excluded.market_index_name},
    )
    with get_session() as session:
        session.execute(stmt)
    return len(rows)


def get_index_name_for_sector(sector: str) -> str | None:
    with get_session() as session:
        row = session.execute(
            select(SectorIndexMapping.market_index_name).where(SectorIndexMapping.companies_sector == sector)
        ).scalar_one_or_none()
        return row


if __name__ == "__main__":
    seed_sector_index_mapping()
