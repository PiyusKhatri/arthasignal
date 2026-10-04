from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Sequence

import numpy as np

from src.simulation.costs import round_trip, sell_costs, shares_for
from src.simulation.protocol import Protocol, as_date, load

BUY = "BUY"
HOLD = "HOLD"
WAIT = "WAIT"
NO_BUY = "NO_BUY"
SELL = "SELL"

GRADED = "graded"
UNFILLED = "unfilled"
PENDING = "pending"
UNGRADED = "ungraded"

NO_TRADE = "no_trade"
LOCKED_UPPER = "locked_upper_circuit"
REACHES_HOLDOUT = "reaches_holdout"
LEARNING_EMBARGO = "learning_embargo"
SELL_CALL = "sell_call"
FLOORSHEET_DERIVED = "floorsheet_derived"

OPENED = "opened"
CLOSES_BUY = "closes_buy"
BLOCKED = "blocked"
NOT_OPEN = "not_open"


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    prev_close: float | None = None
    source: str = "daily_prices"

    @property
    def traded(self) -> bool:
        return self.volume > 0 and self.close > 0


@dataclass(frozen=True)
class Call:
    symbol: str
    call_type: str
    call_date: date
    reference_price: float
    horizon_class: str
    holding_sessions: int
    target: float | None = None
    stop: float | None = None
    score: float | None = None
    position_entry_price: float | None = None
    position_shares: int | None = None

    @property
    def reference(self) -> float:
        return self.position_entry_price if self.position_entry_price is not None else self.reference_price


@dataclass(frozen=True)
class Exit:
    index: int
    price: float
    reason: str
    delay: int


@dataclass(frozen=True)
class Outcome:
    call: Call
    status: str
    correct: bool | None
    reason: str
    entry_date: date | None = None
    entry_price: float | None = None
    exit_date: date | None = None
    exit_price: float | None = None
    exit_delay: int | None = None
    net_pnl: float | None = None
    net_return: float | None = None
    total_costs: float | None = None
    mae: float | None = None
    hold_exit_date: date | None = None
    hold_net_pnl: float | None = None
    hold_net_return: float | None = None
    missed_buy: bool | None = None
    missed_sell: bool | None = None


def effective_bar(bar: Bar, protocol: Protocol) -> Bar:
    if bar.day >= protocol.real_open_start:
        return bar
    fields = protocol.floorsheet_fields() if bar.source == FLOORSHEET_DERIVED else ()
    return replace(
        bar,
        open=bar.open if "open" in fields else bar.close,
        high=max(bar.high, bar.close) if "high" in fields else bar.close,
        low=min(bar.low, bar.close) if "low" in fields else bar.close,
    )


def locked(bar: Bar, protocol: Protocol, direction: int) -> bool:
    if not bar.traded or bar.prev_close is None or bar.prev_close <= 0 or bar.high != bar.low:
        return False
    move = bar.close / bar.prev_close - 1.0
    edge = protocol.circuit_limit(bar.day) - protocol.lock_tolerance
    return move >= edge if direction > 0 else move <= -edge


def _validate(call: Call, protocol: Protocol) -> None:
    if call.call_type not in protocol.raw["calls"]["types"]:
        raise ValueError(f"unknown call type {call.call_type}")
    low, high = protocol.holding_range(call.horizon_class)
    if not low <= call.holding_sessions <= high:
        raise ValueError(f"{call.horizon_class} holding must be {low}..{high} sessions, got {call.holding_sessions}")
    if call.call_type in protocol.stop_required and call.stop is None:
        raise ValueError(f"{call.call_type} calls must carry a stop")
    if call.call_type != SELL and (call.position_entry_price is not None or call.position_shares is not None):
        raise ValueError("only a SELL closing an open BUY uses the position's entry price")


def entry(path: Sequence[Bar | None], protocol: Protocol) -> tuple[int, float] | str:
    bar = path[0] if path else None
    if bar is None or not bar.traded:
        return NO_TRADE
    if locked(bar, protocol, +1):
        return LOCKED_UPPER
    return 0, effective_bar(bar, protocol).open


def long_exit(
    path: Sequence[Bar | None],
    entry_index: int,
    holding: int,
    target: float | None,
    stop: float | None,
    protocol: Protocol,
    sell_after: date | None = None,
) -> Exit | None:
    entry_bar = path[entry_index]
    assert entry_bar is not None
    first = entry_index + protocol.settlement_sessions(entry_bar.day)
    last = entry_index + holding - 1
    pending: tuple[str, int] | None = None
    for i in range(first, len(path)):
        bar = path[i]
        due = i >= last
        sell_due = sell_after is not None and bar is not None and bar.day > sell_after
        if bar is None or not bar.traded:
            if pending is None and sell_due:
                pending = (SELL_CALL, i)
            elif due and pending is None:
                pending = ("horizon", last)
            continue
        e = effective_bar(bar, protocol)
        if locked(bar, protocol, -1):
            if pending is None:
                if stop is not None and e.low <= stop:
                    pending = ("stop", i)
                elif sell_due:
                    pending = (SELL_CALL, i)
                elif due:
                    pending = ("horizon", last)
            continue
        if pending is not None:
            return Exit(i, e.open, pending[0], i - pending[1])
        if stop is not None and e.open <= stop:
            return Exit(i, e.open, "stop", 0)
        if sell_due:
            return Exit(i, e.open, SELL_CALL, 0)
        if target is not None and e.open >= target:
            return Exit(i, e.open, "target", 0)
        if stop is not None and e.low <= stop:
            return Exit(i, stop, "stop", 0)
        if target is not None and e.high >= target:
            return Exit(i, target, "target", 0)
        if due:
            return Exit(i, e.close, "horizon", 0)
    return None


def adverse_excursion(path: Sequence[Bar | None], entry_index: int, entry_price: float, exit: Exit, protocol: Protocol) -> float:
    lowest = min(entry_price, exit.price)
    for i in range(entry_index, exit.index):
        bar = path[i]
        if bar is not None and bar.traded:
            lowest = min(lowest, effective_bar(bar, protocol).low)
    return lowest / entry_price - 1.0


def observe_exit(
    path: Sequence[Bar | None],
    holding: int,
    target: float | None,
    stop: float | None,
    protocol: Protocol,
) -> Exit | None:
    last_seen: Exit | None = None
    for i in range(min(holding, len(path))):
        bar = path[i]
        if bar is None or not bar.traded:
            continue
        e = effective_bar(bar, protocol)
        if stop is not None and e.open >= stop:
            return Exit(i, e.open, "stop", 0)
        if target is not None and e.open <= target:
            return Exit(i, e.open, "target", 0)
        if stop is not None and e.high >= stop:
            return Exit(i, stop, "stop", 0)
        if target is not None and e.low <= target:
            return Exit(i, target, "target", 0)
        last_seen = Exit(i, e.close, "horizon", 0)
    if len(path) < holding:
        return None
    return last_seen


def _day(path: Sequence[Bar | None], index: int) -> date:
    bar = path[index]
    assert bar is not None
    return bar.day


def _held(call: Call, path: Sequence[Bar | None], protocol: Protocol) -> tuple[Any, Exit | None, Any]:
    filled = entry(path, protocol)
    if isinstance(filled, str):
        return filled, None, None
    index, price = filled
    hold = long_exit(path, index, call.holding_sessions, None, None, protocol)
    trip = round_trip(price, hold.price, _day(path, index), _day(path, hold.index), protocol) if hold else None
    return filled, hold, trip


def _grade_buy(call: Call, path: Sequence[Bar | None], protocol: Protocol, sell_after: date | None = None) -> Outcome:
    filled = entry(path, protocol)
    if isinstance(filled, str):
        wrong = protocol.raw["grading"]["BUY"]["unfilled"] == "wrong"
        return Outcome(call, UNFILLED, False if wrong else None, filled)
    index, price = filled
    entry_day = _day(path, index)
    exit = long_exit(path, index, call.holding_sessions, call.target, call.stop, protocol, sell_after)
    if exit is None:
        return Outcome(call, PENDING, None, PENDING, entry_date=entry_day, entry_price=price)
    trip = round_trip(price, exit.price, entry_day, _day(path, exit.index), protocol)
    _, hold, hold_trip = _held(call, path, protocol)
    return Outcome(
        call,
        GRADED,
        exit.reason != "stop" and trip.net_pnl > 0,
        exit.reason,
        entry_date=entry_day,
        entry_price=price,
        exit_date=_day(path, exit.index),
        exit_price=exit.price,
        exit_delay=exit.delay,
        net_pnl=trip.net_pnl,
        net_return=trip.net_return,
        total_costs=trip.total_costs,
        mae=adverse_excursion(path, index, price, exit, protocol),
        hold_exit_date=_day(path, hold.index) if hold else None,
        hold_net_pnl=hold_trip.net_pnl if hold_trip else None,
        hold_net_return=hold_trip.net_return if hold_trip else None,
    )


def _grade_no_buy(call: Call, path: Sequence[Bar | None], protocol: Protocol) -> Outcome:
    filled, hold, trip = _held(call, path, protocol)
    if isinstance(filled, str):
        return Outcome(call, UNGRADED, None, filled)
    index, price = filled
    if hold is None:
        return Outcome(call, PENDING, None, PENDING, entry_date=_day(path, index), entry_price=price)
    return Outcome(
        call,
        GRADED,
        trip.net_pnl < 0,
        "horizon",
        entry_date=_day(path, index),
        entry_price=price,
        exit_date=_day(path, hold.index),
        exit_price=hold.price,
        exit_delay=hold.delay,
        net_pnl=trip.net_pnl,
        net_return=trip.net_return,
        total_costs=trip.total_costs,
        hold_exit_date=_day(path, hold.index),
        hold_net_pnl=trip.net_pnl,
        hold_net_return=trip.net_return,
    )


def sell_threshold(call: Call, protocol: Protocol) -> tuple[int, float]:
    shares = call.position_shares if call.position_shares is not None else shares_for(call.reference, protocol)
    cost = sell_costs(shares, call.reference, call.call_date, protocol).total
    return shares, call.reference - cost / shares


def _grade_sell(call: Call, path: Sequence[Bar | None], protocol: Protocol) -> Outcome:
    exit = observe_exit(path, call.holding_sessions, call.target, call.stop, protocol)
    if exit is None:
        status = PENDING if len(path) < call.holding_sessions else UNGRADED
        return Outcome(call, status, None, PENDING if status == PENDING else NO_TRADE)
    shares, threshold = sell_threshold(call, protocol)
    return Outcome(
        call,
        GRADED,
        exit.price < threshold,
        exit.reason,
        exit_date=_day(path, exit.index),
        exit_price=exit.price,
        exit_delay=exit.delay,
        total_costs=shares * (call.reference - threshold),
    )


def _grade_hold(call: Call, path: Sequence[Bar | None], protocol: Protocol) -> Outcome:
    for i in range(min(call.holding_sessions, len(path))):
        bar = path[i]
        if bar is None or not bar.traded:
            continue
        e = effective_bar(bar, protocol)
        if call.stop is not None and e.low <= call.stop:
            price = min(e.open, call.stop)
            return Outcome(call, GRADED, False, "stop", exit_date=bar.day, exit_price=price)
        if e.close >= call.reference:
            return Outcome(call, GRADED, True, "recovered", exit_date=bar.day, exit_price=e.close)
    if len(path) < call.holding_sessions:
        return Outcome(call, PENDING, None, PENDING)
    seen = [bar.day for bar in path[: call.holding_sessions] if bar is not None]
    return Outcome(call, GRADED, False, "not_recovered", exit_date=max(seen) if seen else None)


def _grade_wait(call: Call, path: Sequence[Bar | None], protocol: Protocol) -> Outcome:
    filled, hold, trip = _held(call, path, protocol)
    seen = observe_exit(path, call.holding_sessions, None, None, protocol)
    return Outcome(
        call,
        UNGRADED,
        None,
        WAIT,
        missed_buy=None if trip is None else trip.net_pnl > 0,
        missed_sell=None if seen is None else seen.price < call.reference,
    )


GRADERS = {BUY: _grade_buy, NO_BUY: _grade_no_buy, SELL: _grade_sell, HOLD: _grade_hold, WAIT: _grade_wait}


def grade(
    call: Call,
    path: Sequence[Bar | None],
    protocol: Protocol | None = None,
    sessions_before_holdout: int | None = None,
    sell_after: date | None = None,
    sessions_before_learning_end: int | None = None,
) -> Outcome:
    p = protocol or load()
    _validate(call, p)
    if sessions_before_learning_end is not None:
        learning_end = p.learning_end
        if call.call_date > learning_end:
            raise ValueError(f"call date {call.call_date} is not in the learning period")
        if not learning_call_allowed(call, sessions_before_learning_end):
            raise ValueError(f"{LEARNING_EMBARGO}: the holding period would cross {learning_end}, so the call is not made")
        for bar in path:
            if bar is not None and bar.day > learning_end:
                raise ValueError(f"path session {bar.day} is after the learning period")
    for bar in path:
        if bar is not None and bar.day <= call.call_date:
            raise ValueError(f"path session {bar.day} is not after the call date {call.call_date}")
        if bar is not None and bar.day >= p.holdout_start:
            raise ValueError(f"path session {bar.day} is in the holdout")
    if sell_after is not None and call.call_type != BUY:
        raise ValueError("only a BUY can be closed by a SELL call")
    if sessions_before_holdout is not None and call.holding_sessions > sessions_before_holdout:
        return Outcome(call, UNGRADED, None, REACHES_HOLDOUT)
    if call.call_type == BUY:
        outcome = _grade_buy(call, path, p, sell_after)
    else:
        outcome = GRADERS[call.call_type](call, path, p)
    if outcome.status == PENDING and sessions_before_holdout is not None and len(path) >= sessions_before_holdout:
        return replace(outcome, status=UNGRADED, correct=None, reason=REACHES_HOLDOUT)
    if outcome.status == PENDING and sessions_before_learning_end is not None and len(path) >= sessions_before_learning_end:
        return replace(outcome, status=UNGRADED, correct=None, reason=LEARNING_EMBARGO)
    return outcome


def learning_call_allowed(call: Call, sessions_before_learning_end: int) -> bool:
    return call.holding_sessions <= sessions_before_learning_end


def last_price_date(outcome: Outcome) -> date | None:
    days = [d for d in (outcome.exit_date, outcome.hold_exit_date) if d is not None]
    return max(days) if days else None


def hidden(outcome: Outcome, completed_runs: set[str] | frozenset[str], protocol: Protocol | None = None) -> bool:
    p = protocol or load()
    used = last_price_date(outcome)
    if outcome.status != GRADED or used is None:
        return False
    own = p.period_of(outcome.call.call_date)
    return any(
        name != own and outcome.call.call_date < start <= used and name not in completed_runs
        for name, start, _ in p.exam_periods()
    )


def position_call(call: Call, buy: Outcome, protocol: Protocol | None = None) -> Call:
    if buy.entry_price is None:
        return call
    p = protocol or load()
    return replace(call, position_entry_price=buy.entry_price, position_shares=shares_for(buy.entry_price, p))


class OpenCalls:
    def __init__(self, protocol: Protocol | None = None) -> None:
        self.protocol = protocol or load()
        self.open: dict[str, Call] = {}

    def admit(self, call: Call) -> tuple[str, Call | None]:
        rules = self.protocol.raw["open_calls"]
        current = self.open.get(call.symbol)
        if current is not None:
            if rules["sell_closes_buy"] and current.call_type == BUY and call.call_type == SELL:
                self.open[call.symbol] = call
                return CLOSES_BUY, current
            return BLOCKED, current
        if call.call_type not in rules["open_types"]:
            return NOT_OPEN, None
        self.open[call.symbol] = call
        return OPENED, None

    def resolve(self, call: Call) -> None:
        if self.open.get(call.symbol) == call:
            del self.open[call.symbol]


def accuracy(outcomes: Sequence[Outcome]) -> dict[str, Any]:
    counted = [o for o in outcomes if o.correct is not None]
    right = sum(1 for o in counted if o.correct)
    return {"graded": len(counted), "right": right, "accuracy": right / len(counted) if counted else None}


def _tail_mean(values: Sequence[float], share: float) -> float:
    ordered = sorted(values)
    k = max(1, math.ceil(len(ordered) * share))
    return float(np.mean(ordered[:k]))


def max_drawdown(returns: Sequence[float]) -> float:
    peak = 0.0
    total = 0.0
    worst = 0.0
    for value in returns:
        total += value
        peak = max(peak, total)
        worst = max(worst, peak - total)
    return worst


def risk_control(outcomes: Sequence[Outcome], protocol: Protocol | None = None) -> dict[str, Any]:
    p = protocol or load()
    share = float(p.raw["risk_control"]["tail_share"])
    rows = [
        o for o in outcomes
        if o.call.call_type == BUY and o.status == GRADED and o.net_return is not None and o.hold_net_return is not None
    ]
    if not rows:
        return {"calls": 0, "score": None}
    stops = [o for o in rows if o.reason == "stop"]
    actual = [o.net_return for o in sorted(rows, key=lambda o: (o.exit_date, o.call.symbol))]
    hold = [o.hold_net_return for o in sorted(rows, key=lambda o: (o.hold_exit_date, o.call.symbol))]
    tail_actual = _tail_mean(actual, share)
    tail_hold = _tail_mean(hold, share)
    score = None
    if tail_hold < 0:
        score = max(-100.0, min(100.0, 100.0 * (tail_actual - tail_hold) / abs(tail_hold)))
    excursions = [-o.mae for o in rows if o.mae is not None]
    return {
        "calls": len(rows),
        "stop_exits": len(stops),
        "loss_avoided_npr": float(sum(o.net_pnl - o.hold_net_pnl for o in stops)),
        "stop_regret_rate": (sum(1 for o in stops if o.hold_net_pnl > 0) / len(stops)) if stops else None,
        "tail_mean_actual": tail_actual,
        "tail_mean_hold": tail_hold,
        "max_drawdown_actual": max_drawdown(actual),
        "max_drawdown_hold": max_drawdown(hold),
        "mae_mean": float(np.mean(excursions)) if excursions else None,
        "mae_p95": float(np.percentile(excursions, 95)) if excursions else None,
        "score": score,
    }
