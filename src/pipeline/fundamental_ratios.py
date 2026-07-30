from __future__ import annotations

from decimal import Decimal


def eps_growth_rate(current_eps: Decimal, prior_eps: Decimal) -> Decimal | None:
    if prior_eps == 0:
        return None
    return (current_eps - prior_eps) / abs(prior_eps) * 100


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
