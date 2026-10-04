from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from src.backtest import event_study as es
from src.league import bots as league_bots
from src.league import leaderboard as lb
from src.league import run as league_run
from src.scorecard import daily
from src.scorecard.audit import lookahead_audit

SYMBOLS = [f"S{i:02d}" for i in range(24)]
SECTORS = {s: f"SEC{i % 4}" for i, s in enumerate(SYMBOLS)}
NO_ACTIONS = pd.DataFrame(columns=["symbol", "action_date", "action_type", "ratio_or_amount"])


def _sessions(start: date, count: int) -> list[date]:
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _prices(sessions, trend: float, seed: int = 3, volume: float = 100_000.0):
    rng = np.random.default_rng(seed)
    rows = []
    for symbol in SYMBOLS:
        close = 100.0
        drift = trend + rng.normal(0, 0.001)
        for day in sessions:
            prev = close
            close = prev * float(np.exp(rng.normal(drift, 0.012)))
            rows.append((symbol, day, prev, max(prev, close), min(prev, close), close, volume, volume * close))
    return pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "volume", "turnover"])


def _index(prices):
    level = prices.groupby("date")["close"].mean().rename("close").reset_index()
    level["open"] = level["close"]
    return level


def _setup(trend: float = 0.0015, volume: float = 100_000.0):
    sessions = _sessions(date(2019, 1, 1), 330)
    prices = _prices(sessions, trend, volume=volume)
    panel = es.build_panel(prices, NO_ACTIONS, SECTORS, sessions=sessions)
    return sessions, prices, panel


def test_six_bots_are_declared_with_distinct_names_and_versions() -> None:
    names = [(b.name, b.version) for b in league_bots.BOTS]
    assert len(names) == 6 and len(set(names)) == 6
    assert {b.side for b in league_bots.BOTS} == {"buy", "avoid"}


def test_every_bot_passes_the_lookahead_audit() -> None:
    sessions, prices, panel = _setup()
    strategies = league_bots.strategies(_index(prices), NO_ACTIONS, set())
    audits = lookahead_audit(strategies, panel, prices, NO_ACTIONS, SECTORS, sample=5, first_index=260)
    assert not any(a["leaky"] for a in audits.values())


def test_buy_bots_respect_the_daily_and_sector_caps() -> None:
    sessions, prices, panel = _setup()
    league = league_bots.League(_index(prices), NO_ACTIONS, set())
    for name in ("bot_momentum", "bot_ranker_spec", "bot_market_timer", "bot_combined"):
        picks = league.selector(name)(panel, 320)
        assert len(picks) <= league_bots.RISK["max_calls_per_day"]
        sectors = pd.Series([SECTORS[s] for s in picks["symbol"]])
        assert (sectors.value_counts() <= league_bots.RISK["max_calls_per_sector"]).all()
    assert not league.select_ranker(panel, 320).empty


def test_illiquid_stocks_are_never_called() -> None:
    sessions, prices, panel = _setup(volume=100.0)
    league = league_bots.League(_index(prices), NO_ACTIONS, set())
    assert league.select_momentum(panel, 320).empty
    assert league.select_ranker(panel, 320).empty


def test_momentum_and_timer_abstain_in_a_bear_market() -> None:
    sessions, prices, panel = _setup(trend=-0.004)
    league = league_bots.League(_index(prices), NO_ACTIONS, set())
    assert league.select_momentum(panel, 320).empty
    assert league.select_timer(panel, 320).empty
    assert league.select_combined(panel, 320).empty


def test_combined_bot_drops_avoid_hits() -> None:
    sessions, prices, panel = _setup(trend=0.0005)
    base = league_bots.League(_index(prices), NO_ACTIONS, set()).select_combined(panel, 320)
    assert not base.empty
    target = base["symbol"].iloc[0]
    actions = pd.DataFrame({"symbol": [target], "action_date": [sessions[315]], "action_type": ["BONUS"], "ratio_or_amount": [10.0]})
    league = league_bots.League(_index(prices), actions, set())
    assert target not in set(league.select_combined(panel, 320)["symbol"])
    assert target in set(league.select_avoid(panel, 320)["symbol"])


def _graded(strategy: str, version: str, correct, baseline, gross, days):
    return pd.DataFrame({
        "call_id": range(len(days)), "strategy": strategy, "model_version": version, "symbol": "X", "signal_date": days,
        "probability": np.nan, "horizon": 5, "status": "filled", "exit_date": days, "gross_return": gross,
        "universe_mean": 0.01, "baseline_share": baseline, "correct": correct, "failure_cause": None, "situations": [["all"]] * len(days),
    })


def test_avoid_edge_is_buy_baseline_minus_buy_correctness() -> None:
    days = _sessions(date(2026, 1, 1), 40)
    graded = _graded("bot_avoid_e2e4", "b2", [False] * 30 + [True] * 10, [0.4] * 40, [-0.05] * 40, days)
    board = lb.leaderboard(graded, days, tests=104, horizons=(5,))
    row = next(r for r in board[5] if r["bot"] == "bot_avoid_e2e4")
    assert row["edge"] == pytest.approx(0.4 - 0.25, abs=1e-4)
    assert row["avoided_excess_expectancy_1pct"] < 0
    assert row["verdict"] == "INSUFFICIENT SAMPLE"


def test_cohort_drawdown_uses_non_overlapping_dates() -> None:
    days = _sessions(date(2026, 1, 1), 30)
    gross = [0.11 if i % 2 == 0 else -0.5 for i in range(30)]
    frame = _graded("bot_momentum", "b2", [True] * 30, [0.3] * 30, gross, days)
    index = {d: i for i, d in enumerate(days)}
    out = lb.cohort_drawdown(frame, 5, index)
    assert out["cohorts"] == 5
    assert out["max_drawdown"] == 0.0
    assert out["final_equity"] == pytest.approx(1.1 ** 5, abs=1e-4)


def test_empty_ledger_gives_an_insufficient_leaderboard() -> None:
    board = lb.leaderboard(pd.DataFrame(), [], tests=104)
    assert set(board) == set(lb.LEADERBOARD_HORIZONS)
    assert all(r["verdict"] == "INSUFFICIENT SAMPLE" for rows in board.values() for r in rows)


@pytest.fixture
def temp_schema():
    from src.database.connection import engine

    name = f"league_test_{uuid.uuid4().hex[:8]}"
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f"CREATE SCHEMA {name}")
    except Exception:
        pytest.skip("database not reachable")
    league_run.apply_league_schema(engine, name)
    yield engine, name
    with engine.begin() as connection:
        connection.exec_driver_sql(f"DROP SCHEMA {name} CASCADE")


def test_league_write_is_idempotent_append_only_and_deadline_bound(temp_schema) -> None:
    from sqlalchemy import text

    engine, schema = temp_schema
    today = datetime.now(tz=daily.NPT).date()
    now = datetime.now(tz=daily.NPT)
    calls = pd.DataFrame({"strategy": ["bot_momentum", "bot_avoid_e2e4"], "model_version": ["b2", "b2"], "symbol": ["NABIL", "NICA"],
                          "signal_date": [today, today], "score": [0.1, None], "situations": [["all"], ["all"]],
                          "feature_hash": ["a" * 64, "b" * 64]})
    assert league_run.write_calls(engine, calls, now, schema) == {"attempted": 2, "inserted": 2}
    assert league_run.write_calls(engine, calls, now, schema) == {"attempted": 2, "inserted": 0}
    with pytest.raises(TimeoutError):
        league_run.write_calls(engine, calls.assign(signal_date=today - timedelta(days=3)), now, schema)
    board = lb.leaderboard(pd.DataFrame(), [today], tests=104, horizons=(5,))
    assert league_run.write_leaderboard(engine, board, today, schema) == 6
    with pytest.raises(Exception):
        with engine.begin() as connection:
            connection.execute(text(f"DELETE FROM {schema}.league_leaderboard"))
    registered = league_run.register_bots(engine, schema)
    assert len(registered) == 6
