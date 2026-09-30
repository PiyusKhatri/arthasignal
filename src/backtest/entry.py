from __future__ import annotations

from datetime import date
from typing import Mapping, Sequence

from src.backtest.types import (
    EXIT_BLOCKED,
    EXIT_DELAYED,
    EXIT_ON_TIME,
    EXIT_STRANDED,
    Bar,
    Label,
)


class EntryRuleViolation(Exception):
    pass


def _valid(price: float | None) -> bool:
    return price is not None and price > 0


def assert_entry_after_signal(label: Label, signal_date: date) -> None:
    if label.entry_date <= signal_date:
        raise EntryRuleViolation(
            f"entry on {label.entry_date} is not after signal date {signal_date}; "
            "entry must be the next session open"
        )
    if label.exit_date < label.entry_date:
        raise EntryRuleViolation(f"exit on {label.exit_date} precedes entry on {label.entry_date}")


def next_open_label(
    market_sessions: Sequence[date],
    bars: Mapping[date, Bar],
    signal_date: date,
    horizon_sessions: int = 20,
    grace_sessions: int = 3,
) -> Label | None:
    try:
        signal_index = market_sessions.index(signal_date)
    except ValueError:
        return None

    entry_index = signal_index + 1
    target_index = signal_index + horizon_sessions
    if target_index + grace_sessions >= len(market_sessions):
        return None

    entry_date = market_sessions[entry_index]
    entry_bar = bars.get(entry_date)
    if entry_bar is None or not _valid(entry_bar.open):
        return None
    entry_price = float(entry_bar.open)

    for index in range(target_index, len(market_sessions)):
        exit_bar = bars.get(market_sessions[index])
        if exit_bar is None or not _valid(exit_bar.close):
            continue
        delay = index - target_index
        if delay == 0:
            status = EXIT_ON_TIME
        elif delay <= grace_sessions:
            status = EXIT_DELAYED
        else:
            status = EXIT_BLOCKED
        exit_price = float(exit_bar.close)
        label = Label(
            entry_date=entry_date,
            entry_price=entry_price,
            exit_date=market_sessions[index],
            exit_price=exit_price,
            gross_return=exit_price / entry_price - 1.0,
            exit_status=status,
            blocked_sessions=delay,
        )
        assert_entry_after_signal(label, signal_date)
        return label

    last_close = entry_bar.close if _valid(entry_bar.close) else entry_price
    for index in range(entry_index + 1, target_index):
        bar = bars.get(market_sessions[index])
        if bar is not None and _valid(bar.close):
            last_close = bar.close
    label = Label(
        entry_date=entry_date,
        entry_price=entry_price,
        exit_date=market_sessions[-1],
        exit_price=float(last_close),
        gross_return=float(last_close) / entry_price - 1.0,
        exit_status=EXIT_STRANDED,
        blocked_sessions=len(market_sessions) - 1 - target_index,
    )
    assert_entry_after_signal(label, signal_date)
    return label


def benchmark_return(index_bars: Mapping[date, Bar], label: Label) -> float | None:
    entry_bar = index_bars.get(label.entry_date)
    exit_bar = index_bars.get(label.exit_date)
    if entry_bar is None or exit_bar is None:
        return None
    if not _valid(entry_bar.open) or not _valid(exit_bar.close):
        return None
    return float(exit_bar.close) / float(entry_bar.open) - 1.0
