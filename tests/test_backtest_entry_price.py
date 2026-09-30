from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.backtest.entry import EntryRuleViolation, assert_entry_after_signal, benchmark_return, next_open_label
from src.backtest.report import selection_report
from src.backtest.config import load_holdout_config
from src.backtest.types import (
    EXIT_BLOCKED,
    EXIT_DELAYED,
    EXIT_ON_TIME,
    EXIT_STRANDED,
    Bar,
    Label,
    Row,
)

CONFIG = load_holdout_config()
SESSIONS = [date(2024, 1, 1) + timedelta(days=offset) for offset in range(60)]
SIGNAL = SESSIONS[5]


def _bars(missing: set[int] = frozenset()) -> dict[date, Bar]:
    return {
        session: Bar(open=100.0 + index, close=100.5 + index)
        for index, session in enumerate(SESSIONS)
        if index not in missing
    }


def test_entry_is_the_next_session_open_never_the_signal_day_close() -> None:
    bars = _bars()
    bars[SIGNAL] = Bar(open=50.0, close=60.0)
    label = next_open_label(SESSIONS, bars, SIGNAL, horizon_sessions=20, grace_sessions=3)

    assert label is not None
    assert label.entry_date == SESSIONS[6]
    assert label.entry_date > SIGNAL
    assert label.entry_price == bars[SESSIONS[6]].open == 106.0
    assert label.entry_price != bars[SIGNAL].close
    assert label.exit_date == SESSIONS[25]
    assert label.exit_price == bars[SESSIONS[25]].close == 125.5
    assert label.gross_return == pytest.approx(125.5 / 106.0 - 1.0)
    assert label.exit_status == EXIT_ON_TIME


def test_signal_day_prices_cannot_change_the_label() -> None:
    first = next_open_label(SESSIONS, _bars(), SIGNAL)
    bars = _bars()
    bars[SIGNAL] = Bar(open=1.0, close=9999.0)
    second = next_open_label(SESSIONS, bars, SIGNAL)
    assert first == second


def test_missing_next_session_open_makes_the_row_ungradable() -> None:
    assert next_open_label(SESSIONS, _bars(missing={6}), SIGNAL) is None
    bars = _bars()
    bars[SESSIONS[6]] = Bar(open=None, close=106.5)
    assert next_open_label(SESSIONS, bars, SIGNAL) is None


def test_entry_is_not_moved_to_a_later_trade() -> None:
    bars = _bars(missing={6})
    assert SESSIONS[7] in bars
    assert next_open_label(SESSIONS, bars, SIGNAL) is None


def test_label_is_unresolved_until_the_grace_window_is_observable() -> None:
    assert next_open_label(SESSIONS[:28], _bars(), SIGNAL, horizon_sessions=20, grace_sessions=3) is None
    assert next_open_label(SESSIONS[:29], _bars(), SIGNAL, horizon_sessions=20, grace_sessions=3) is not None


def test_same_day_entry_is_rejected() -> None:
    same_day = Label(
        entry_date=SIGNAL,
        entry_price=100.0,
        exit_date=SESSIONS[25],
        exit_price=110.0,
        gross_return=0.10,
        exit_status=EXIT_ON_TIME,
    )
    with pytest.raises(EntryRuleViolation):
        assert_entry_after_signal(same_day, SIGNAL)
    with pytest.raises(EntryRuleViolation):
        selection_report(
            [Row(symbol="AAA", signal_date=SIGNAL, label=same_day)],
            {("AAA", SIGNAL)},
            CONFIG,
            variants_tried=1,
        )


def test_exit_within_grace_is_delayed() -> None:
    label = next_open_label(SESSIONS, _bars(missing={25, 26}), SIGNAL)
    assert label.exit_date == SESSIONS[27]
    assert label.exit_status == EXIT_DELAYED
    assert label.blocked_sessions == 2


def test_blocked_sell_waits_for_the_next_real_print() -> None:
    label = next_open_label(SESSIONS, _bars(missing=set(range(25, 40))), SIGNAL)
    assert label.exit_status == EXIT_BLOCKED
    assert label.exit_date == SESSIONS[40]
    assert label.exit_price == 140.5
    assert label.blocked_sessions == 15


def test_stranded_position_is_marked_at_the_last_traded_close() -> None:
    label = next_open_label(SESSIONS, _bars(missing=set(range(20, 60))), SIGNAL)
    assert label.exit_status == EXIT_STRANDED
    assert label.exit_price == 119.5
    assert label.exit_date == SESSIONS[-1]


def test_benchmark_uses_the_same_entry_open_and_exit_close() -> None:
    label = next_open_label(SESSIONS, _bars(), SIGNAL)
    index_bars = {session: Bar(open=2000.0 + i, close=2001.0 + i) for i, session in enumerate(SESSIONS)}
    assert benchmark_return(index_bars, label) == pytest.approx(2026.0 / 2006.0 - 1.0)
    del index_bars[label.exit_date]
    assert benchmark_return(index_bars, label) is None


def test_report_counts_blocked_sells() -> None:
    rows = []
    for day, missing in ((SESSIONS[5], set()), (SESSIONS[6], set(range(26, 45)))):
        label = next_open_label(SESSIONS, _bars(missing=missing), day)
        rows.append(Row(symbol="AAA", signal_date=day, label=label, benchmark_return=0.0, momentum_score=1.0))
    report = selection_report(rows, {row.key for row in rows}, CONFIG, variants_tried=1)
    assert report["selected_calls"] == 2
    assert report["exit_statuses"] == {EXIT_ON_TIME: 1, EXIT_BLOCKED: 1}
    assert report["blocked_sell_rate"] == 0.5
