from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from src.backtest import price_integrity as pi


def _sessions(start: date, count: int) -> list[date]:
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


SESSIONS = _sessions(date(2019, 1, 1), 60)
NO_ACTIONS = pd.DataFrame(columns=["symbol", "action_date", "action_type", "ratio_or_amount"])


def _series(symbol: str, closes, days=None):
    days = days or SESSIONS[: len(closes)]
    return pd.DataFrame({"symbol": symbol, "date": days, "open": closes, "close": closes})


def test_moves_inside_the_band_are_not_steps() -> None:
    prices = _series("AAA", [100, 110, 99.5, 104.0])
    assert pi.detect_steps(prices, NO_ACTIONS, SESSIONS).empty


def test_tolerance_and_band() -> None:
    assert not pi.is_step(0.104, date(2020, 1, 1))
    assert pi.is_step(0.11, date(2020, 1, 1))
    assert not pi.is_step(0.14, date(2026, 5, 1))
    assert pi.is_step(-0.16, date(2026, 5, 1))


def test_recorded_bonus_resolves_the_step() -> None:
    prices = _series("AAA", [120, 120, 100, 101])
    actions = pd.DataFrame({"symbol": ["AAA"], "action_date": [SESSIONS[2]], "action_type": ["BONUS"], "ratio_or_amount": [20.0]})
    steps = pi.detect_steps(prices, actions, SESSIONS)
    assert list(steps["kind"]) == [pi.RESOLVED]


def test_unrecorded_bonus_is_unresolved_and_wrong_ratio_is_a_mismatch() -> None:
    prices = _series("AAA", [120, 120, 80, 81])
    steps = pi.detect_steps(prices, NO_ACTIONS, SESSIONS)
    assert list(steps["kind"]) == [pi.UNRESOLVED]
    actions = pd.DataFrame({"symbol": ["AAA"], "action_date": [SESSIONS[2]], "action_type": ["BONUS"], "ratio_or_amount": [10.0]})
    assert list(pi.detect_steps(prices, actions, SESSIONS)["kind"]) == [pi.MISMATCH]


def test_action_without_a_price_drop_is_an_overshoot() -> None:
    prices = _series("AAA", [100, 100, 100, 100])
    actions = pd.DataFrame({"symbol": ["AAA"], "action_date": [SESSIONS[2]], "action_type": ["BONUS"], "ratio_or_amount": [50.0]})
    assert list(pi.detect_steps(prices, actions, SESSIONS)["kind"]) == [pi.OVERSHOOT]


def test_long_halt_explains_a_jump_but_short_gap_does_not() -> None:
    days_long = [SESSIONS[0], SESSIONS[1], SESSIONS[1 + pi.HALT_GAP_SESSIONS]]
    long_gap = pi.detect_steps(_series("AAA", [100, 100, 70], days_long), NO_ACTIONS, SESSIONS)
    assert list(long_gap["kind"]) == [pi.HALT]
    days_short = [SESSIONS[0], SESSIONS[1], SESSIONS[5]]
    short_gap = pi.detect_steps(_series("BBB", [100, 100, 70], days_short), NO_ACTIONS, SESSIONS)
    assert list(short_gap["kind"]) == [pi.UNRESOLVED]


def test_unresolved_mask_marks_only_unresolved_kinds() -> None:
    prices = pd.concat([_series("AAA", [120, 120, 80, 81]), _series("BBB", [120, 120, 100, 101])])
    actions = pd.DataFrame({"symbol": ["BBB"], "action_date": [SESSIONS[2]], "action_type": ["BONUS"], "ratio_or_amount": [20.0]})
    steps = pi.detect_steps(prices, actions, SESSIONS)
    mask = pi.unresolved_mask(steps, ["AAA", "BBB"], len(SESSIONS))
    assert mask[0, 2] and not mask[1].any()
    assert mask.sum() == 1


def test_check_recent_reports_only_recent_unresolved_steps() -> None:
    prices = pd.concat([_series("AAA", [120, 120, 80] + [80] * 50), _series("BBB", [100] * 55 + [60, 60])])
    steps = pi.detect_steps(prices, NO_ACTIONS, SESSIONS)
    recent = pi.check_recent(steps, SESSIONS, 10)
    assert [r["symbol"] for r in recent] == ["BBB"]


def test_theoretical_drop() -> None:
    assert np.isclose(pi.theoretical_drop("BONUS", 25.0, 200.0), 0.2)
    assert np.isclose(pi.theoretical_drop("RIGHT", 100.0, 300.0), 1 - (300 + 100) / 2 / 300)


def test_data_quality_check_fails_loudly(monkeypatch) -> None:
    import pytest

    from src.pipeline import data_quality as dq

    step = {"symbol": "AAA", "date": "2025-01-10", "raw_move": "-0.3", "adjusted_move": "-0.3", "kind": pi.UNRESOLVED}
    monkeypatch.setattr(dq, "recent_unresolved_price_steps", lambda latest_date, lookback=20: [step])
    assert dq._check_unresolved_price_steps(date(2025, 1, 10))["flagged"]
    with pytest.raises(dq.UnresolvedPriceStepsError, match="AAA"):
        dq.assert_no_unresolved_price_steps(date(2025, 1, 10))
    monkeypatch.setattr(dq, "recent_unresolved_price_steps", lambda latest_date, lookback=20: [])
    assert not dq._check_unresolved_price_steps(date(2025, 1, 10))["flagged"]
    dq.assert_no_unresolved_price_steps(date(2025, 1, 10))
