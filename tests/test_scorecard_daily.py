from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from src.backtest import event_study as es
from src.scorecard import daily, model_v0
from src.scorecard.audit import lookahead_audit

SYMBOLS = [f"S{i:02d}" for i in range(15)]
SECTORS = {s: "A" for s in SYMBOLS}
NO_ACTIONS = pd.DataFrame(columns=["symbol", "action_date", "action_type", "ratio_or_amount"])


FROZEN_NOW = datetime(2026, 9, 30, 13, 0, tzinfo=daily.NPT)


def _sessions(start: date, count: int) -> list[date]:
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _prices(sessions, trend: float, seed: int = 2):
    rng = np.random.default_rng(seed)
    rows = []
    for k, symbol in enumerate(SYMBOLS):
        close = 100.0
        for day in sessions:
            prev = close
            close = prev * float(np.exp(rng.normal(trend, 0.015)))
            rows.append((symbol, day, prev, max(prev, close), min(prev, close), close, 1000.0, 1000.0 * close))
    return pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "volume", "turnover"])


def _index(prices):
    level = prices.groupby("date")["close"].mean().rename("close").reset_index()
    level["open"] = level["close"]
    return level


def test_entry_deadline_is_next_day_market_open_in_kathmandu() -> None:
    deadline = daily.entry_deadline(date(2026, 10, 1))
    assert deadline.isoformat() == "2026-10-02T11:00:00+05:45"


def test_model_v0_passes_the_lookahead_audit() -> None:
    sessions = _sessions(date(2019, 1, 1), 320)
    prices = _prices(sessions, 0.001)
    panel = es.build_panel(prices, NO_ACTIONS, SECTORS, sessions=sessions)
    strategy = model_v0.strategy(_index(prices), NO_ACTIONS, set())
    audits = lookahead_audit([strategy], panel, prices, NO_ACTIONS, SECTORS, sample=6, first_index=250)
    assert not audits[model_v0.NAME]["leaky"]
    assert not strategy.select(panel, 300).empty


def test_model_v0_abstains_in_a_bear_market() -> None:
    sessions = _sessions(date(2019, 1, 1), 320)
    prices = _prices(sessions, -0.004)
    panel = es.build_panel(prices, NO_ACTIONS, SECTORS, sessions=sessions)
    strategy = model_v0.strategy(_index(prices), NO_ACTIONS, set())
    assert strategy.select(panel, 300).empty


def test_model_v0_skips_recent_bonus_book_close() -> None:
    sessions = _sessions(date(2019, 1, 1), 320)
    prices = _prices(sessions, 0.001)
    panel = es.build_panel(prices, NO_ACTIONS, SECTORS, sessions=sessions)
    base = model_v0.strategy(_index(prices), NO_ACTIONS, set()).select(panel, 300)
    target = base["symbol"].iloc[0]
    actions = pd.DataFrame({"symbol": [target], "action_date": [sessions[295]], "action_type": ["BONUS"], "ratio_or_amount": [10.0]})
    after = model_v0.strategy(_index(prices), actions, set()).select(panel, 300)
    assert target not in set(after["symbol"])


@pytest.fixture
def temp_schema():
    from src.database.connection import engine
    from src.scorecard.schema import apply_schema

    name = f"scorecard_test_{uuid.uuid4().hex[:8]}"
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f"CREATE SCHEMA {name}")
    except Exception:
        pytest.skip("database not reachable")
    apply_schema(engine, name)
    yield engine, name
    with engine.begin() as connection:
        connection.exec_driver_sql(f"DROP SCHEMA {name} CASCADE")


def _calls(signal_date: date) -> pd.DataFrame:
    return pd.DataFrame(
        {"symbol": ["NABIL", "NICA"], "signal_date": [signal_date, signal_date], "score": [0.12, 0.08],
         "situations": [["all", "market_bull"], ["all"]], "feature_hash": ["a" * 64, "b" * 64]}
    )


def test_live_write_is_idempotent_and_refused_after_the_deadline(temp_schema) -> None:
    from sqlalchemy import text

    engine, schema = temp_schema
    now = FROZEN_NOW
    today = now.date()
    first = daily.write_live_calls(engine, _calls(today), now, schema)
    second = daily.write_live_calls(engine, _calls(today), now, schema)
    assert first == {"attempted": 2, "inserted": 2}
    assert second == {"attempted": 2, "inserted": 0}
    with engine.connect() as connection:
        rows = connection.execute(text(f"SELECT count(*), bool_and(mode = 'live'), bool_and(probability IS NULL) FROM {schema}.scorecard_calls")).one()
    assert tuple(rows) == (2, True, True)
    with pytest.raises(TimeoutError):
        daily.write_live_calls(engine, _calls(today - timedelta(days=3)), now, schema)


def test_model_registry_is_append_only(temp_schema) -> None:
    from sqlalchemy import text

    engine, schema = temp_schema
    with engine.begin() as connection:
        connection.execute(
            text(
                f"INSERT INTO {schema}.scorecard_models (model_name, model_version, description, parameters, parameters_hash, code_commit) "
                "VALUES ('model_v0', 'v0', 'd', '{}'::jsonb, :h, 'abc')"
            ),
            {"h": "0" * 64},
        )
    with pytest.raises(Exception, match="append-only"):
        with engine.begin() as connection:
            connection.exec_driver_sql(f"UPDATE {schema}.scorecard_models SET description = 'x'")


def test_quarantined_symbols_are_excluded_from_live_calls(monkeypatch) -> None:
    from types import SimpleNamespace

    picks = pd.DataFrame({"symbol": ["AAA", "BBB", "CCC"], "score": [0.3, 0.2, 0.1]})
    monkeypatch.setattr(daily.model_v0, "strategy", lambda *a: SimpleNamespace(select=lambda panel, t: picks))
    monkeypatch.setattr(daily, "situation_matrix", lambda *a: None)
    monkeypatch.setattr(daily, "labels_for", lambda *a: [])
    panel = SimpleNamespace(sessions=(date(2025, 1, 15), date(2025, 1, 16)), row={"AAA": 0, "BBB": 1, "CCC": 2})
    state = {"panel": panel, "inputs": {"index": None, "rates": None}, "actions": NO_ACTIONS, "mergers": set(), "market": None}
    calls = daily.compute_calls(state, {"BBB": "2025-01-10 unresolved"})
    assert list(calls["symbol"]) == ["AAA", "CCC"]
    assert state["quarantine_excluded"] == ["BBB"]


def test_avoid_hits_report_both_rules() -> None:
    sessions = _sessions(date(2019, 1, 1), 320)
    prices = _prices(sessions, 0.001)
    up = prices["symbol"] == "S03"
    for k, day in enumerate(sessions[290:294]):
        mask = up & (prices["date"] == day)
        prices.loc[mask, "close"] = 100.0 * 1.1 ** (k + 1)
        prices.loc[mask, "high"] = prices.loc[mask, "close"]
    later = up & prices["date"].isin(sessions[294:])
    prices.loc[later, "close"] = 100.0 * 1.1 ** 4
    actions = pd.DataFrame({"symbol": ["S01"], "action_date": [sessions[295]], "action_type": ["BONUS"], "ratio_or_amount": [10.0]})
    panel = es.build_panel(prices, actions, SECTORS, sessions=sessions)
    hits = model_v0.ModelV0(_index(prices), actions, set()).avoid_hits(panel, 300)
    assert ("S01", model_v0.AVOID_E2) in set(zip(hits["symbol"], hits["rule"]))
    assert set(hits["rule"]) <= set(model_v0.AVOID_STRATEGIES)
    quiet = model_v0.ModelV0(_index(prices), NO_ACTIONS, set()).avoid_hits(panel, 300)
    assert model_v0.AVOID_E2 not in set(quiet["rule"])


def test_avoid_observations_are_written_with_their_rule(temp_schema) -> None:
    from sqlalchemy import text

    engine, schema = temp_schema
    now = FROZEN_NOW
    today = now.date()
    buys = _calls(today).assign(strategy=model_v0.NAME)
    avoid = pd.DataFrame({"symbol": ["ABC"], "signal_date": [today], "score": [None], "situations": [["all"]],
                          "feature_hash": ["c" * 64], "strategy": [model_v0.AVOID_E2]})
    result = daily.write_live_calls(engine, pd.concat([buys, avoid], ignore_index=True), now, schema)
    assert result == {"attempted": 3, "inserted": 3}
    with engine.connect() as connection:
        rows = dict(connection.execute(text(f"SELECT strategy, count(*) FROM {schema}.scorecard_calls GROUP BY 1")).all())
    assert rows == {model_v0.NAME: 2, model_v0.AVOID_E2: 1}


def test_holdout_dates_other_than_the_latest_session_are_refused(monkeypatch) -> None:
    from src.database.holdout_guard import HoldoutQueryViolation

    with pytest.raises(HoldoutQueryViolation):
        daily.run(date(2025, 10, 5), dry_run=True)
