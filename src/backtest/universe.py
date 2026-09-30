from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models import Company, DailyPrice

STATUS_ACTIVE = "A"
STATUS_DELISTED = "D"
STATUS_SUSPENDED = "S"


def survivorship_coverage(
    companies: Iterable[tuple[str, str | None]],
    priced_symbols: Iterable[str],
) -> dict[str, Any]:
    priced = set(priced_symbols)
    by_status: dict[str, dict[str, int]] = {}
    for symbol, status in companies:
        bucket = by_status.setdefault(status or "unknown", {"listed": 0, "with_prices": 0})
        bucket["listed"] += 1
        if symbol in priced:
            bucket["with_prices"] += 1

    def _fraction(status: str) -> float | None:
        bucket = by_status.get(status)
        if not bucket or bucket["listed"] == 0:
            return None
        return bucket["with_prices"] / bucket["listed"]

    non_active_listed = sum(b["listed"] for s, b in by_status.items() if s != STATUS_ACTIVE)
    non_active_priced = sum(b["with_prices"] for s, b in by_status.items() if s != STATUS_ACTIVE)
    return {
        "by_status": by_status,
        "active_price_coverage": _fraction(STATUS_ACTIVE),
        "delisted_price_coverage": _fraction(STATUS_DELISTED),
        "suspended_price_coverage": _fraction(STATUS_SUSPENDED),
        "non_active_listed": non_active_listed,
        "non_active_with_prices": non_active_priced,
        "non_active_price_coverage": non_active_priced / non_active_listed if non_active_listed else None,
    }


def load_universe(session: Session, instrument_type: str = "Equity") -> list[tuple[str, str | None]]:
    rows = session.execute(
        select(Company.symbol, Company.status)
        .where(Company.instrument_type == instrument_type)
        .order_by(Company.symbol)
    ).all()
    return [(symbol, status) for symbol, status in rows]


def load_survivorship_coverage(session: Session, instrument_type: str = "Equity") -> dict[str, Any]:
    companies = load_universe(session, instrument_type)
    priced = session.execute(select(DailyPrice.symbol).distinct()).scalars().all()
    return survivorship_coverage(companies, priced)
