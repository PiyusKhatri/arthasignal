from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from src.database.connection import get_session
from src.database.models import CorporateAction

DILUTION_FLAG_ACTION_TYPES = {"bonus", "right", "split"}

DILUTION_WARNING = (
    "growth rate may reflect share-count dilution from a bonus/right/split event in this period, "
    "not genuine earnings change - interpret with caution"
)


def eps_growth_rate(current_eps: Decimal, prior_eps: Decimal) -> Decimal | None:
    if prior_eps == 0:
        return None
    return (current_eps - prior_eps) / abs(prior_eps) * 100


def flag_dilution_affected_growth(symbol: str, period_start: date, period_end: date) -> dict[str, Any]:
    with get_session() as session:
        rows = session.execute(
            select(CorporateAction.action_date, CorporateAction.action_type, CorporateAction.ratio_or_amount)
            .where(CorporateAction.symbol == symbol)
            .where(CorporateAction.action_date > period_start)
            .where(CorporateAction.action_date <= period_end)
            .order_by(CorporateAction.action_date)
        ).all()

    events = [
        {
            "action_date": row.action_date,
            "action_type": row.action_type.value if hasattr(row.action_type, "value") else row.action_type,
            "ratio_or_amount": row.ratio_or_amount,
        }
        for row in rows
        if (row.action_type.value if hasattr(row.action_type, "value") else row.action_type)
        in DILUTION_FLAG_ACTION_TYPES
    ]

    return {
        "dilution_flag": bool(events),
        "dilution_events": events,
        "warning": DILUTION_WARNING if events else None,
    }


def eps_growth_rate_with_dilution_context(
    symbol: str, current_eps: Decimal, prior_eps: Decimal, period_start: date, period_end: date
) -> dict[str, Any]:
    growth = eps_growth_rate(current_eps, prior_eps)
    dilution = flag_dilution_affected_growth(symbol, period_start, period_end)
    return {"eps_growth_rate": growth, **dilution}


def pe_ratio(price: Decimal, eps: Decimal) -> Decimal | None:
    if eps <= 0:
        return None
    return price / eps


def sector_relative_valuation(company_pe: Decimal | None, sector_avg_pe: Decimal | None) -> Decimal | None:
    if company_pe is None or sector_avg_pe is None or sector_avg_pe == 0:
        return None
    return (company_pe - sector_avg_pe) / sector_avg_pe * 100


def debt_to_equity(total_debt: Decimal, total_equity: Decimal) -> Decimal | None:
    if total_equity <= 0:
        return None
    return total_debt / total_equity
