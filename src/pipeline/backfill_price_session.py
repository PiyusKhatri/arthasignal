from __future__ import annotations

import argparse
import json
import logging
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import Company, CorporateAction, DailyPrice
from src.pipeline.adjustment_ops import reapply_adjustment_for_symbol
from src.scrapers import sharesansar_scraper

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PRICE_COLUMNS = ("symbol", "date", "open", "high", "low", "close", "volume", "turnover")


def _row_count(session_date: date) -> int:
    with get_session() as session:
        return session.execute(
            select(func.count()).select_from(DailyPrice).where(DailyPrice.date == session_date)
        ).scalar_one()


def _known_symbols() -> set[str]:
    with get_session() as session:
        return set(session.execute(select(Company.symbol)).scalars().all())


def is_valid_price_row(row: dict[str, Any]) -> bool:
    if not str(row.get("symbol") or "").strip():
        return False
    values = [row.get(k) for k in ("open", "high", "low", "close")]
    if any(v is None or v <= 0 for v in values):
        return False
    _, high, low, _ = values
    return high >= low and row.get("volume", 0) > 0


def insert_price_rows_only_new(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    payload = [{k: row[k] for k in PRICE_COLUMNS} for row in rows]
    stmt = (
        pg_insert(DailyPrice)
        .values(payload)
        .on_conflict_do_nothing(index_elements=["symbol", "date"])
        .returning(DailyPrice.id)
    )
    with get_session() as session:
        return len(session.execute(stmt).fetchall())


def _symbols_with_actions(symbols: set[str]) -> list[str]:
    with get_session() as session:
        return sorted(
            set(
                session.execute(
                    select(CorporateAction.symbol).where(CorporateAction.symbol.in_(symbols)).distinct()
                ).scalars().all()
            )
        )


def backfill_price_session(session_date: date) -> dict[str, Any]:
    before = _row_count(session_date)
    as_of, rows = sharesansar_scraper.get_session_prices(session_date)
    if as_of != session_date:
        raise ValueError(f"source returned session {as_of}, expected {session_date}")

    known = _known_symbols()
    valid = [r for r in rows if is_valid_price_row(r)]
    unknown = sorted({r["symbol"] for r in valid} - known)
    to_insert = [r for r in valid if r["symbol"] in known]
    inserted = insert_price_rows_only_new(to_insert)

    inserted_symbols = {r["symbol"] for r in to_insert}
    adjusted = 0
    for symbol in _symbols_with_actions(inserted_symbols):
        adjusted += 1 if reapply_adjustment_for_symbol(symbol) else 0

    summary = {
        "date": session_date.isoformat(),
        "rows_before": before,
        "source_rows": len(rows),
        "invalid_rows": len(rows) - len(valid),
        "unknown_symbols": unknown,
        "inserted": inserted,
        "existing_kept": len(to_insert) - inserted,
        "symbols_readjusted": adjusted,
        "rows_after": _row_count(session_date),
    }
    logger.info("Price session backfill: %s", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill one price session from the public daily price page")
    parser.add_argument("dates", nargs="+", type=date.fromisoformat)
    args = parser.parse_args()
    results = [backfill_price_session(d) for d in args.dates]
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
