from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from src.database.connection import get_session
from src.database.models import DailyPrice, SignalCallOutcome, SignalCallStatus, TradingCalendar
from src.pipeline import backfill_calendar, grade_signal_calls
from src.pipeline.backfill_calendar import (
    HOLIDAY_NAME,
    WEEKEND_NAME,
    _is_structural_weekend,
    _verified_no_session,
    classify_calendar_day,
)
from src.pipeline.grade_signal_calls import _resolution_target_date
from src.pipeline.regrade_signal_calls import is_miscounted

SWITCH = date(2026, 4, 6)
START = date(2026, 1, 4)
END = date(2026, 6, 30)
HOLIDAY = date(2026, 4, 14)
UNVERIFIED = date(2026, 5, 20)


def _sessions() -> set[date]:
    sessions: set[date] = set()
    day = START
    while day <= END:
        weekday = day.weekday()
        if day < SWITCH:
            trades = weekday in {6, 0, 1, 2, 3}
        else:
            trades = weekday in {0, 1, 2, 3, 4}
        if trades and day not in {HOLIDAY, UNVERIFIED}:
            sessions.add(day)
        day += timedelta(days=1)
    return sessions


def _index_series(sessions: set[date], break_after: date | None = None, stale_rows: tuple[date, ...] = ()):
    dates: list[date] = []
    closes: list[float] = []
    changes: list[float] = []
    level = 2000.0
    for day in sorted(sessions | set(stale_rows)):
        if day in sessions:
            change = 5.0
            level += change
            if break_after is not None and dates and dates[-1] < break_after < day:
                level += 40.0
        else:
            change = 5.0
        dates.append(day)
        closes.append(level)
        changes.append(change)
    return {"NEPSE Index": (dates, closes, changes)}


def _classify(day: date, sessions: set[date], index_series, existing=None, end_date: date = END, attempt=False):
    return classify_calendar_day(
        day,
        sessions=sessions,
        sorted_sessions=sorted(sessions),
        index_series=index_series,
        existing=existing,
        end_date=end_date,
        attempt_confirmed_for_today=attempt,
    )


def test_day_with_prices_is_a_session() -> None:
    sessions = _sessions()
    row = _classify(date(2026, 4, 10), sessions, _index_series(sessions))
    assert date(2026, 4, 10).weekday() == 4
    assert row == {"date": date(2026, 4, 10), "is_trading_day": True, "holiday_name": None, "is_known_holiday": False}


def test_sunday_after_the_switch_to_monday_friday_is_a_weekend() -> None:
    sessions = _sessions()
    for sunday in (date(2026, 4, 12), date(2026, 5, 17), date(2026, 6, 21)):
        assert sunday.weekday() == 6
        assert _is_structural_weekend(sunday, sessions, max(sessions))
        row = _classify(sunday, sessions, _index_series(sessions))
        assert row["is_trading_day"] is False
        assert row["holiday_name"] == WEEKEND_NAME
        assert row["is_known_holiday"] is False


def test_friday_before_the_switch_is_a_weekend_and_after_it_is_not() -> None:
    sessions = _sessions()
    assert _is_structural_weekend(date(2026, 3, 27), sessions, max(sessions))
    assert not _is_structural_weekend(date(2026, 5, 1), sessions, max(sessions))
    assert not _is_structural_weekend(date(2026, 3, 1), sessions, max(sessions))


def test_index_only_day_is_not_a_session() -> None:
    sessions = _sessions()
    stale_sunday = date(2026, 5, 10)
    index_series = _index_series(sessions, stale_rows=(stale_sunday, HOLIDAY))
    assert stale_sunday in index_series["NEPSE Index"][0]
    assert _classify(stale_sunday, sessions, index_series)["is_trading_day"] is False
    holiday_row = _classify(HOLIDAY, sessions, index_series)
    assert holiday_row["is_trading_day"] is False
    assert holiday_row["is_known_holiday"] is True


def test_weekday_without_prices_and_continuous_index_is_a_known_holiday() -> None:
    sessions = _sessions()
    index_series = _index_series(sessions)
    assert _verified_no_session(HOLIDAY, date(2026, 4, 15), index_series)
    row = _classify(HOLIDAY, sessions, index_series)
    assert row == {"date": HOLIDAY, "is_trading_day": False, "holiday_name": HOLIDAY_NAME, "is_known_holiday": True}


def test_weekday_without_prices_and_broken_index_is_unexplained_not_a_holiday() -> None:
    sessions = _sessions()
    index_series = _index_series(sessions, break_after=UNVERIFIED)
    assert not _verified_no_session(UNVERIFIED, date(2026, 5, 21), index_series)
    row = _classify(UNVERIFIED, sessions, index_series)
    assert row == {"date": UNVERIFIED, "is_trading_day": False, "holiday_name": None, "is_known_holiday": False}


def test_weekday_without_any_index_evidence_is_not_flagged_as_a_holiday() -> None:
    sessions = _sessions()
    row = _classify(UNVERIFIED, sessions, {})
    assert row["is_trading_day"] is False
    assert row["is_known_holiday"] is False
    assert row["holiday_name"] is None


def test_manually_named_holiday_is_preserved_and_automatic_names_are_recomputed() -> None:
    sessions = _sessions()
    index_series = _index_series(sessions, break_after=UNVERIFIED)
    named = _classify(UNVERIFIED, sessions, index_series, existing=(False, "Republic Day"))
    assert named == {
        "date": UNVERIFIED,
        "is_trading_day": False,
        "holiday_name": "Republic Day",
        "is_known_holiday": True,
    }
    relabelled = _classify(HOLIDAY, sessions, _index_series(sessions), existing=(False, WEEKEND_NAME))
    assert relabelled["holiday_name"] == HOLIDAY_NAME


def test_days_after_the_last_session_stay_expected_trading_days() -> None:
    sessions = _sessions()
    today = date(2026, 7, 1)
    assert today.weekday() == 2
    assert _classify(today, sessions, _index_series(sessions), end_date=today, attempt=False) is None
    row = _classify(today, sessions, _index_series(sessions), end_date=today, attempt=True)
    assert row["is_trading_day"] is True
    assert row["is_known_holiday"] is False


def test_trading_calendar_has_a_separate_known_holiday_flag() -> None:
    assert "is_known_holiday" in TradingCalendar.__table__.columns
    assert TradingCalendar.__table__.columns["is_known_holiday"].nullable is False


def test_confirmed_market_data_means_prices_not_index_rows(monkeypatch) -> None:
    executed: list[str] = []

    class _Result:
        def scalar_one_or_none(self):
            return None

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement):
            executed.append(str(statement))
            return _Result()

    monkeypatch.setattr(backfill_calendar, "get_session", lambda: _Session())
    assert backfill_calendar._confirmed_market_data_exists(date(2026, 9, 6)) is False
    assert len(executed) == 1
    assert "daily_prices" in executed[0]
    assert "market_index" not in executed[0]


def test_grading_sessions_are_exactly_the_dates_with_prices() -> None:
    with get_session() as session:
        expected = list(session.execute(select(DailyPrice.date).distinct().order_by(DailyPrice.date)).scalars())
    assert grade_signal_calls._load_trading_days() == expected


def test_horizon_counts_only_sessions_with_prices() -> None:
    sessions = sorted(_sessions())
    signal_date = date(2026, 4, 9)
    target = _resolution_target_date(signal_date, 20, sessions)
    window = [day for day in sessions if signal_date < day <= target]
    assert len(window) == 20
    assert HOLIDAY not in window
    assert all(day.weekday() != 6 for day in window)

    with_phantom_days = sorted(set(sessions) | {HOLIDAY} | {date(2026, 4, 12), date(2026, 4, 19), date(2026, 4, 26)})
    assert _resolution_target_date(signal_date, 20, with_phantom_days) < target


def test_call_resolved_before_its_true_target_is_miscounted() -> None:
    sessions = sorted(_sessions())
    signal_date = date(2026, 4, 9)
    target = _resolution_target_date(signal_date, 20, sessions)
    early = {
        "id": 1,
        "entry_date": signal_date,
        "horizon": 20,
        "status": SignalCallStatus.RESOLVED,
        "outcome": SignalCallOutcome.WIN,
        "resolution_date": sessions[sessions.index(target) - 3],
    }
    on_time = {**early, "resolution_date": target}
    too_recent = {**early, "entry_date": sessions[-5], "resolution_date": sessions[-1]}
    void = {**early, "status": SignalCallStatus.VOID, "outcome": SignalCallOutcome.VOID, "resolution_date": None}

    assert is_miscounted(early, sessions) is True
    assert is_miscounted(on_time, sessions) is False
    assert is_miscounted(too_recent, sessions) is True
    assert is_miscounted(void, sessions) is True


def test_call_resolved_after_a_newly_added_session_is_miscounted() -> None:
    sessions = sorted(_sessions())
    signal_date = date(2026, 4, 9)
    target = _resolution_target_date(signal_date, 20, sessions)
    later = sessions[sessions.index(target) + 1]
    call = {
        "id": 1,
        "symbol": "ABC",
        "entry_date": signal_date,
        "horizon": 20,
        "status": SignalCallStatus.RESOLVED,
        "outcome": SignalCallOutcome.WIN,
        "resolution_date": later,
    }

    assert is_miscounted(call, sessions, {"ABC": [signal_date, target, later]}) is True
    assert is_miscounted(call, sessions, {"ABC": [signal_date, later]}) is False
    assert is_miscounted(call, sessions) is False
