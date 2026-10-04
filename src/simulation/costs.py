from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from src.simulation.protocol import Protocol, effective_row, load


@dataclass(frozen=True)
class SideCost:
    amount: float
    commission: float
    sebon_fee: float
    dp_charge: float

    @property
    def total(self) -> float:
        return self.commission + self.sebon_fee + self.dp_charge


@dataclass(frozen=True)
class RoundTrip:
    shares: int
    entry_price: float
    exit_price: float
    buy: SideCost
    sell: SideCost
    gain_before_tax: float
    cgt_rate: float
    cgt: float

    @property
    def cost_basis(self) -> float:
        return self.buy.amount + self.buy.total

    @property
    def gross_return(self) -> float:
        return self.exit_price / self.entry_price - 1.0

    @property
    def total_costs(self) -> float:
        return self.buy.total + self.sell.total + self.cgt

    @property
    def net_pnl(self) -> float:
        return self.gain_before_tax - self.cgt

    @property
    def net_return(self) -> float:
        return self.net_pnl / self.cost_basis


def commission_rate(amount: float, day: date, protocol: Protocol | None = None) -> float:
    p = protocol or load()
    schedule = effective_row(p.raw["costs"]["commission_schedules"], day)
    for tier in schedule["tiers"]:
        if tier["up_to"] is None or amount <= float(tier["up_to"]):
            return float(tier["rate"])
    raise ValueError(f"no commission tier covers {amount}")


def side_cost(amount: float, day: date, protocol: Protocol | None = None) -> SideCost:
    p = protocol or load()
    costs = p.raw["costs"]
    commission = max(float(costs["minimum_commission_npr"]), amount * commission_rate(amount, day, p))
    sebon = amount * float(costs["sebon_fee"]["rate"])
    dp = float(costs["dp_charge"]["npr_per_scrip_per_side"])
    return SideCost(amount=amount, commission=commission, sebon_fee=sebon, dp_charge=dp)


def shares_for(price: float, protocol: Protocol | None = None) -> int:
    p = protocol or load()
    if price <= 0:
        raise ValueError(f"price must be positive, got {price}")
    return max(1, math.floor(float(p.raw["costs"]["notional_npr"]) / price))


def cgt_rate(entry_day: date, exit_day: date, protocol: Protocol | None = None) -> float:
    p = protocol or load()
    tax = p.raw["costs"]["capital_gains_tax"]
    row = effective_row(tax["schedules"], exit_day)
    held = (exit_day - entry_day).days
    return float(row["long"] if held > int(tax["long_term_after_days"]) else row["short"])


def round_trip(
    entry_price: float,
    exit_price: float,
    entry_day: date,
    exit_day: date,
    protocol: Protocol | None = None,
    shares: int | None = None,
) -> RoundTrip:
    p = protocol or load()
    if exit_day < entry_day:
        raise ValueError(f"exit {exit_day} precedes entry {entry_day}")
    quantity = shares if shares is not None else shares_for(entry_price, p)
    buy = side_cost(quantity * entry_price, entry_day, p)
    sell = side_cost(quantity * exit_price, exit_day, p)
    gain = (sell.amount - sell.total) - (buy.amount + buy.total)
    rate = cgt_rate(entry_day, exit_day, p)
    return RoundTrip(
        shares=quantity,
        entry_price=entry_price,
        exit_price=exit_price,
        buy=buy,
        sell=sell,
        gain_before_tax=gain,
        cgt_rate=rate,
        cgt=rate * max(gain, 0.0),
    )
