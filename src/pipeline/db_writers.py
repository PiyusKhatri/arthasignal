from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import (
    Broker,
    Company,
    CorporateAction,
    DailyPrice,
    Fundamental,
    GdpNepse,
    IpoCalendar,
    MarketIndex,
    PromoterHolding,
    Remittance,
    ShortTermInterestRate,
    SymbolHistory,
    TradingCalendar,
)
from src.scrapers import nepse_api

RECENT_UPSERT_WINDOW_DAYS = 2

logger = logging.getLogger(__name__)


def _get_existing_company_data(symbols: list[str]) -> dict[str, tuple[str, str | None]]:
    if not symbols:
        return {}
    with get_session() as session:
        rows = session.execute(
            select(Company.symbol, Company.company_name, Company.sector).where(Company.symbol.in_(symbols))
        ).all()
    return {row.symbol: (row.company_name, row.sector) for row in rows}


def build_company_records(symbols: list[str]) -> list[dict[str, Any]]:
    securities_map: dict[str, dict[str, Any]] = {}
    try:
        securities = nepse_api.get_all_securities()
        securities_map = {s["symbol"]: s for s in securities if s.get("symbol")}
    except Exception:
        logger.exception("Could not fetch securities metadata, falling back to minimal company records")

    existing_data = _get_existing_company_data(symbols)

    records = []
    for symbol in symbols:
        security = securities_map.get(symbol)
        if security:
            company_name = security.get("companyName") or symbol
            sector = security.get("sectorName")
            instrument_type = security.get("instrumentType") or "Equity"
            status = security.get("status") or "A"
        else:
            company_name = symbol
            sector = None
            instrument_type = "Equity"
            status = "A"

        existing = existing_data.get(symbol)
        if existing:
            existing_name, existing_sector = existing

            if company_name == symbol and existing_name and existing_name != symbol:
                logger.warning(
                    "%s: scraped company_name is a placeholder (%r), preserving existing real value %r "
                    "instead of overwriting it",
                    symbol,
                    company_name,
                    existing_name,
                )
                company_name = existing_name

            if not sector and existing_sector:
                logger.warning(
                    "%s: scraped sector is missing, preserving existing real value %r instead of overwriting it",
                    symbol,
                    existing_sector,
                )
                sector = existing_sector

        records.append(
            {
                "symbol": symbol,
                "company_name": company_name,
                "sector": sector,
                "instrument_type": instrument_type,
                "status": status,
            }
        )
    return records


def upsert_companies(records: list[dict[str, Any]]) -> int:
    if not records:
        return 0
    stmt = pg_insert(Company).values(records)
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol"],
        set_={
            "company_name": stmt.excluded.company_name,
            "sector": stmt.excluded.sector,
            "instrument_type": stmt.excluded.instrument_type,
            "status": stmt.excluded.status,
        },
    )
    with get_session() as session:
        session.execute(stmt)
    return len(records)


def insert_new_daily_prices(rows: list[dict[str, Any]]) -> tuple[int, int]:
    if not rows:
        return 0, 0

    cutoff = date.today() - timedelta(days=RECENT_UPSERT_WINDOW_DAYS)
    recent_rows = [r for r in rows if r["date"] >= cutoff]
    older_rows = [r for r in rows if r["date"] < cutoff]

    inserted = 0

    if recent_rows:
        stmt = pg_insert(DailyPrice).values(recent_rows)
        update_columns = {c: getattr(stmt.excluded, c) for c in recent_rows[0] if c not in ("symbol", "date")}
        stmt = stmt.on_conflict_do_update(index_elements=["symbol", "date"], set_=update_columns)
        stmt = stmt.returning(DailyPrice.id, literal_column("(xmax = 0)").label("is_new_insert"))
        with get_session() as session:
            result = session.execute(stmt)
            inserted += sum(1 for row in result if row.is_new_insert)

    if older_rows:
        stmt = pg_insert(DailyPrice).values(older_rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["symbol", "date"])
        stmt = stmt.returning(DailyPrice.id)
        with get_session() as session:
            result = session.execute(stmt)
            inserted += len(result.fetchall())

    skipped = len(rows) - inserted
    return inserted, skipped


def upsert_recent_market_index_rows(rows: list[dict[str, Any]]) -> tuple[int, int]:
    if not rows:
        return 0, 0

    cutoff = date.today() - timedelta(days=RECENT_UPSERT_WINDOW_DAYS)
    recent_rows = [r for r in rows if r["date"] >= cutoff]
    older_rows = [r for r in rows if r["date"] < cutoff]

    inserted = 0

    if recent_rows:
        stmt = pg_insert(MarketIndex).values(recent_rows)
        update_columns = {c: getattr(stmt.excluded, c) for c in recent_rows[0] if c not in ("index_name", "date")}
        stmt = stmt.on_conflict_do_update(index_elements=["index_name", "date"], set_=update_columns)
        stmt = stmt.returning(MarketIndex.id)
        with get_session() as session:
            result = session.execute(stmt)
            inserted += len(result.fetchall())

    if older_rows:
        stmt = pg_insert(MarketIndex).values(older_rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["index_name", "date"])
        stmt = stmt.returning(MarketIndex.id)
        with get_session() as session:
            result = session.execute(stmt)
            inserted += len(result.fetchall())

    skipped = len(rows) - inserted
    return inserted, skipped


def insert_new_market_index_rows(rows: list[dict[str, Any]]) -> tuple[int, int]:
    if not rows:
        return 0, 0
    stmt = pg_insert(MarketIndex).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=["index_name", "date"])
    stmt = stmt.returning(MarketIndex.id)
    with get_session() as session:
        result = session.execute(stmt)
        inserted = len(result.fetchall())
    skipped = len(rows) - inserted
    return inserted, skipped


def insert_new_corporate_actions(rows: list[dict[str, Any]]) -> tuple[int, int]:
    if not rows:
        return 0, 0
    stmt = pg_insert(CorporateAction).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=["symbol", "action_date", "action_type"])
    stmt = stmt.returning(CorporateAction.id)
    with get_session() as session:
        result = session.execute(stmt)
        inserted = len(result.fetchall())
    skipped = len(rows) - inserted
    return inserted, skipped


def insert_new_fundamentals(rows: list[dict[str, Any]]) -> tuple[int, int]:
    if not rows:
        return 0, 0
    stmt = pg_insert(Fundamental).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=["symbol", "reported_date"])
    stmt = stmt.returning(Fundamental.id)
    with get_session() as session:
        result = session.execute(stmt)
        inserted = len(result.fetchall())
    skipped = len(rows) - inserted
    return inserted, skipped


def insert_new_promoter_holding(rows: list[dict[str, Any]]) -> tuple[int, int]:
    if not rows:
        return 0, 0
    stmt = pg_insert(PromoterHolding).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=["symbol", "reported_date"])
    stmt = stmt.returning(PromoterHolding.id)
    with get_session() as session:
        result = session.execute(stmt)
        inserted = len(result.fetchall())
    skipped = len(rows) - inserted
    return inserted, skipped


def insert_new_interest_rates(rows: list[dict[str, Any]]) -> tuple[int, int]:
    if not rows:
        return 0, 0
    stmt = pg_insert(ShortTermInterestRate).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=["fiscal_year", "month"])
    stmt = stmt.returning(ShortTermInterestRate.id)
    with get_session() as session:
        result = session.execute(stmt)
        inserted = len(result.fetchall())
    skipped = len(rows) - inserted
    return inserted, skipped


def insert_new_remittance(rows: list[dict[str, Any]]) -> tuple[int, int]:
    if not rows:
        return 0, 0
    stmt = pg_insert(Remittance).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=["fiscal_year", "month"])
    stmt = stmt.returning(Remittance.id)
    with get_session() as session:
        result = session.execute(stmt)
        inserted = len(result.fetchall())
    skipped = len(rows) - inserted
    return inserted, skipped


def insert_new_gdp_nepse(rows: list[dict[str, Any]]) -> tuple[int, int]:
    if not rows:
        return 0, 0
    stmt = pg_insert(GdpNepse).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=["fiscal_year"])
    stmt = stmt.returning(GdpNepse.id)
    with get_session() as session:
        result = session.execute(stmt)
        inserted = len(result.fetchall())
    skipped = len(rows) - inserted
    return inserted, skipped


IPO_CALENDAR_UPSERT_BATCH_SIZE = 100


def upsert_ipo_calendar(rows: list[dict[str, Any]]) -> tuple[int, int]:
    if not rows:
        return 0, 0

    deduped: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for row in rows:
        deduped[(row["company_name"], row["issue_type"], row["opening_date"])] = row
    rows = list(deduped.values())

    update_columns = {
        c: None
        for c in rows[0]
        if c not in ("company_name", "issue_type", "opening_date", "result_announced_date", "min_application_units")
    }

    upserted = 0
    failed_batches = 0

    for i in range(0, len(rows), IPO_CALENDAR_UPSERT_BATCH_SIZE):
        batch = rows[i : i + IPO_CALENDAR_UPSERT_BATCH_SIZE]
        stmt = pg_insert(IpoCalendar).values(batch)
        batch_update_columns = {c: getattr(stmt.excluded, c) for c in update_columns}
        stmt = stmt.on_conflict_do_update(
            index_elements=["company_name", "issue_type", "opening_date"], set_=batch_update_columns
        )
        try:
            with get_session() as session:
                session.execute(stmt)
            upserted += len(batch)
        except Exception:
            logger.exception("Failed to upsert ipo_calendar batch starting at index %d", i)
            failed_batches += 1

    return upserted, failed_batches


def insert_new_symbol_history(rows: list[dict[str, Any]]) -> tuple[int, int]:
    if not rows:
        return 0, 0
    stmt = pg_insert(SymbolHistory).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=["old_symbol", "new_symbol", "event_type"])
    stmt = stmt.returning(SymbolHistory.id)
    with get_session() as session:
        result = session.execute(stmt)
        inserted = len(result.fetchall())
    skipped = len(rows) - inserted
    return inserted, skipped


def upsert_brokers(records: list[dict[str, Any]]) -> int:
    if not records:
        return 0
    stmt = pg_insert(Broker).values(records)
    stmt = stmt.on_conflict_do_update(
        index_elements=["broker_id"],
        set_={"broker_name": stmt.excluded.broker_name, "is_active": stmt.excluded.is_active},
    )
    with get_session() as session:
        session.execute(stmt)
    return len(records)


def upsert_trading_calendar_rows(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(TradingCalendar).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["date"],
        set_={
            "is_trading_day": stmt.excluded.is_trading_day,
            "holiday_name": stmt.excluded.holiday_name,
        },
    )
    with get_session() as session:
        session.execute(stmt)
    return len(rows)
