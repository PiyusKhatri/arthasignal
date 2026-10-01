from __future__ import annotations

from datetime import date, timedelta

from src.backtest.config import load_holdout_config
from src.backtest.signal_rerun import FIRST_REAL_OPEN_SESSION, discontinuity_dates, event_inside_window, label_for
from src.backtest.types import Bar

D = [date(2024, 1, 1) + timedelta(days=i) for i in range(40)]


def test_event_window_is_after_signal_and_up_to_exit() -> None:
    events = [D[3], D[10]]

    assert event_inside_window(events, D[2], D[3]) is True
    assert event_inside_window(events, D[3], D[9]) is False
    assert event_inside_window(events, D[4], D[12]) is True
    assert event_inside_window([], D[0], D[39]) is False


def test_moves_beyond_the_circuit_limit_are_flagged_as_discontinuities() -> None:
    bars = [
        {"date": D[0], "close": 100},
        {"date": D[1], "close": 109},
        {"date": D[2], "close": 60},
        {"date": D[3], "close": 61},
    ]

    assert discontinuity_dates(bars) == [D[2]]


def test_label_enters_next_session_open_and_exits_at_horizon_close() -> None:
    config = load_holdout_config()
    bars = {day: Bar(open=100.0 + i, close=101.0 + i) for i, day in enumerate(D)}
    index = {day: i for i, day in enumerate(D)}

    label = label_for(D[0], index, D, bars, config)

    assert label.entry_date == D[1]
    assert label.entry_price == 101.0
    assert label.exit_date == D[config.horizon_sessions]
    assert label.exit_price == 101.0 + config.horizon_sessions


def test_label_is_missing_without_a_next_session_trade_or_enough_sessions() -> None:
    config = load_holdout_config()
    bars = {day: Bar(open=100.0, close=100.0) for day in D if day != D[1]}
    index = {day: i for i, day in enumerate(D)}

    assert label_for(D[0], index, D, bars, config) is None
    assert label_for(D[30], index, D, {day: Bar(open=1.0, close=1.0) for day in D}, config) is None
    assert label_for(date(2030, 1, 1), index, D, bars, config) is None


def test_signals_start_when_real_opening_prices_start() -> None:
    assert FIRST_REAL_OPEN_SESSION == date(2018, 2, 18)


def test_new_listing_window_counts_sessions_from_first_price() -> None:
    from src.backtest.signal_rerun import is_new_listing
    from src.backtest.types import Row

    sessions = {day: i for i, day in enumerate(D)}
    first = {"NEW": D[0], "OLD": date(2014, 6, 1)}

    assert is_new_listing(Row(symbol="NEW", signal_date=D[5]), first, sessions) is True
    assert is_new_listing(Row(symbol="OLD", signal_date=D[5]), first, sessions) is False
    assert is_new_listing(Row(symbol="MISSING", signal_date=D[5]), first, sessions) is False
