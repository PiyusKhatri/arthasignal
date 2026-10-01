from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from src.backtest import broker_flow_spec as spec
from src.backtest.broker_flow_eval import (
    ENTRY_CLOSE,
    ENTRY_OPEN,
    evaluate_hypothesis,
    label_for,
    nepse_return,
    select_portfolio,
)
from src.backtest.types import Bar


def _sessions(start: date, count: int) -> list[date]:
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _bars(sessions):
    open_bars = {d: Bar(open=100.0 + i + 0.5, close=100.0 + i) for i, d in enumerate(sessions)}
    close_bars = {d: Bar(open=b.close, close=b.close) for d, b in open_bars.items()}
    return open_bars, close_bars


def test_real_open_period_enters_at_next_open_and_exits_at_t_plus_20_close() -> None:
    sessions = _sessions(date(2019, 1, 1), 60)
    open_bars, close_bars = _bars(sessions)
    index = {d: i for i, d in enumerate(sessions)}
    label, rule = label_for(sessions[5], index, sessions, open_bars, close_bars)
    assert rule == ENTRY_OPEN
    assert label.entry_date == sessions[6] and label.entry_price == open_bars[sessions[6]].open
    assert label.exit_date == sessions[25]


def test_before_real_opens_enters_at_next_close_and_holds_twenty_sessions() -> None:
    sessions = _sessions(date(2016, 1, 1), 60)
    open_bars, close_bars = _bars(sessions)
    index = {d: i for i, d in enumerate(sessions)}
    label, rule = label_for(sessions[5], index, sessions, open_bars, close_bars)
    assert rule == ENTRY_CLOSE
    assert label.entry_date == sessions[6] and label.entry_price == open_bars[sessions[6]].close
    assert label.exit_date == sessions[26]
    assert label.entry_date > sessions[5]


def test_no_label_when_exit_would_fall_after_the_last_session() -> None:
    sessions = _sessions(date(2024, 11, 1), 30)
    open_bars, close_bars = _bars(sessions)
    index = {d: i for i, d in enumerate(sessions)}
    assert label_for(sessions[10], index, sessions, open_bars, close_bars)[0] is None


def test_nepse_benchmark_uses_close_to_close_before_real_opens() -> None:
    sessions = _sessions(date(2016, 1, 1), 40)
    open_bars, close_bars = _bars(sessions)
    index = {d: i for i, d in enumerate(sessions)}
    label, rule = label_for(sessions[2], index, sessions, open_bars, close_bars)
    nepse = {d: Bar(open=1.0, close=float(i + 1)) for i, d in enumerate(sessions)}
    assert nepse_return(nepse, label, rule) == (index[label.exit_date] + 1) / (index[label.entry_date] + 1) - 1


def test_portfolio_takes_the_predicted_quintile_with_symbol_tie_break() -> None:
    frame = pd.DataFrame({"symbol": [f"S{i:02d}" for i in range(26)], "f": [1.0] * 2 + list(range(24))})
    high = select_portfolio(frame, "f", "high")
    low = select_portfolio(frame, "f", "low")
    assert len(high) == 6 and len(low) == 6
    assert list(high["f"]) == [23, 22, 21, 20, 19, 18]
    assert list(low["symbol"])[:3] == ["S02", "S00", "S01"]


def _daily(excess: float, folds=(1, 2, 3, 4), dates_per_fold=60, noise=0.0):
    rng = np.random.default_rng(1)
    records = []
    start = date(2018, 3, 1)
    for f in folds:
        for j in range(dates_per_fold):
            d = start + timedelta(days=len(records))
            universe = 0.01
            records.append(
                {
                    "date": d, "n": 50, "k": 10, "universe": universe,
                    "portfolio": universe + spec.PRIMARY_COST + excess + rng.normal(0, noise),
                    "nepse": 0.0, "momentum": universe, "opposite": universe, "ic": 0.1,
                    "entry_rule": ENTRY_OPEN, "symbols": ("A",), "fold": f, "block": len(records) // 20,
                }
            )
    return pd.DataFrame(records)


def test_gates_pass_only_with_a_clear_positive_excess() -> None:
    strong = evaluate_hypothesis(_daily(0.02, noise=0.001), 0.01, 0.05 / 31)
    assert strong["passes"] and all(strong["gates"].values())
    weak = evaluate_hypothesis(_daily(0.0, noise=0.01), 0.01, 0.05 / 31)
    assert not weak["gates"]["G1"]
    assert not weak["passes"]


def test_short_folds_do_not_count_as_positive() -> None:
    result = evaluate_hypothesis(_daily(0.02, dates_per_fold=40, noise=0.001), 0.01, 0.05 / 31)
    assert not result["gates"]["G3"]
    assert not result["passes"]
