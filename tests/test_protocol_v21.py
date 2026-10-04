from __future__ import annotations

import uuid
from datetime import date, datetime

import pandas as pd
import pytest

from src.scorecard import calendar, daily, v2
from src.scorecard.calendar import NPT

CAL = calendar.parse({
    "weekday_rules": [{"effective_from": "2014-01-01", "weekdays": ["Sun", "Mon", "Tue", "Wed", "Thu"]},
                      {"effective_from": "2026-04-08", "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri"]}],
    "holidays": ["2026-10-20"],
})


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=NPT)


def test_protocol_is_v21_with_unchanged_grading() -> None:
    assert v2.PROTOCOL_VERSION == "accuracy-v2.1"
    assert v2.GRADE_VERSION == "accuracy-v2"


def test_deadline_is_the_next_session_open_not_the_next_calendar_day() -> None:
    assert CAL.next_open(date(2026, 10, 2)) == at(date(2026, 10, 5), 11)
    assert CAL.next_open(date(2026, 10, 1)) == at(date(2026, 10, 2), 11)
    assert CAL.next_open(date(2026, 10, 19)) == at(date(2026, 10, 21), 11)
    assert CAL.next_open(date(2025, 1, 16)) == at(date(2025, 1, 19), 11)
    assert CAL.next_open(date(2026, 10, 2), [date(2026, 10, 3)]) == at(date(2026, 10, 3), 11)


def test_config_calendar_uses_monday_to_friday_now() -> None:
    assert daily.entry_deadline(date(2026, 10, 2)) == at(date(2026, 10, 5), 11)


def test_weekend_and_holiday_posts_map_to_the_last_session_and_stay_writable_until_the_next_open() -> None:
    sessions = [date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 2)]
    saturday = at(date(2026, 10, 3), 20)
    assert calendar.signal_date_for(saturday, sessions, CAL) == date(2026, 10, 2)
    assert saturday < CAL.next_open(date(2026, 10, 2), sessions)
    assert calendar.signal_date_for(at(date(2026, 10, 5), 10, 30), sessions, CAL) == date(2026, 10, 2)
    assert calendar.signal_date_for(at(date(2026, 10, 5), 12), sessions, CAL) is None
    assert calendar.signal_date_for(at(date(2026, 10, 1), 10), sessions, CAL) == date(2026, 9, 30)
    assert calendar.signal_date_for(at(date(2026, 9, 29), 12), sessions, CAL) is None


@pytest.fixture
def temp_schema():
    from src.database.connection import engine
    from src.scorecard.schema import apply_schema

    name = f"v21_test_{uuid.uuid4().hex[:8]}"
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f"CREATE SCHEMA {name}")
    except Exception:
        pytest.skip("database not reachable")
    apply_schema(engine, name)
    yield engine, name
    with engine.begin() as connection:
        connection.exec_driver_sql(f"DROP SCHEMA {name} CASCADE")


def _insert(engine, schema, signal: date, created: datetime, symbol: str = "NABIL") -> None:
    from sqlalchemy import text

    with engine.begin() as connection:
        connection.execute(
            text(f"INSERT INTO {schema}.scorecard_calls (call_uid, mode, strategy, model_version, feature_hash, batch_id, symbol, "
                 "signal_date, probability, score, situations, created_at) VALUES (gen_random_uuid(), 'live', 's', 'v', :h, 'b', :sym, :d, "
                 "NULL, NULL, ARRAY['all'], :c)"),
            {"h": "a" * 64, "sym": symbol, "d": signal, "c": created},
        )


def test_database_enforces_the_next_session_open(temp_schema) -> None:
    engine, schema = temp_schema
    _insert(engine, schema, date(2026, 10, 2), at(date(2026, 10, 4), 18), "NABIL")
    _insert(engine, schema, date(2026, 10, 2), at(date(2026, 10, 5), 10, 59), "NICA")
    with pytest.raises(Exception, match="accuracy-v2.1"):
        _insert(engine, schema, date(2026, 10, 2), at(date(2026, 10, 5), 11), "HRL")
    _insert(engine, schema, date(2025, 1, 16), at(date(2025, 1, 18), 12), "API")
    with pytest.raises(Exception, match="accuracy-v2.1"):
        _insert(engine, schema, date(2025, 1, 16), at(date(2025, 1, 19), 11, 30), "CIT")


def test_league_writer_accepts_a_saturday_write_for_friday(temp_schema) -> None:
    from src.league import run as league_run

    engine, schema = temp_schema
    calls = pd.DataFrame({"strategy": ["bot_momentum"], "model_version": ["b2"], "symbol": ["NABIL"], "signal_date": [date(2026, 10, 2)],
                          "score": [0.1], "situations": [["all"]], "feature_hash": ["c" * 64]})
    assert league_run.write_calls(engine, calls, at(date(2026, 10, 3), 9), schema)["inserted"] == 1
    with pytest.raises(TimeoutError):
        league_run.write_calls(engine, calls.assign(symbol="NICA"), at(date(2026, 10, 5), 11, 1), schema)
