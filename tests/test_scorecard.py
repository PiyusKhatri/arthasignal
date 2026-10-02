from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from src.backtest import event_study as es
from src.scorecard import spec
from src.scorecard.audit import lookahead_audit
from src.scorecard.grading import BLOCKED, FILLED, UNFILLED, build_cube, build_market, grade_calls
from src.scorecard.metrics import INSUFFICIENT, cell_metrics
from src.scorecard.situations import market_state_labels, situation_matrix
from src.scorecard.strategies import STRATEGIES

SYMBOLS = [f"S{i:02d}" for i in range(20)]
SECTORS = {s: ("Banks" if i % 2 else "Hydro") for i, s in enumerate(SYMBOLS)}
NO_ACTIONS = pd.DataFrame(columns=["symbol", "action_date", "action_type", "ratio_or_amount"])
RATES = pd.DataFrame(columns=["fiscal_year", "month", "treasury_bill_rate", "interbank_commercial_rate"])


def _sessions(start: date, count: int) -> list[date]:
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _prices(sessions, seed=5):
    rng = np.random.default_rng(seed)
    rows = []
    for symbol in SYMBOLS:
        close = 100.0
        for day in sessions:
            prev = close
            close = prev * float(np.exp(rng.normal(0.0005, 0.015)))
            opening = prev * float(np.exp(rng.normal(0, 0.004)))
            rows.append((symbol, day, opening, max(opening, close) * 1.004, min(opening, close) * 0.996, close, 1000.0, 1000.0 * close))
    return pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "volume", "turnover"])


def _index(prices):
    level = prices.groupby("date")["close"].mean().rename("close").reset_index()
    level["open"] = level["close"]
    level["high"] = level["close"]
    level["low"] = level["close"]
    return level


def _market(prices, sessions):
    panel = es.build_panel(prices, NO_ACTIONS, SECTORS, sessions=sessions)
    return build_market(panel, NO_ACTIONS, _index(prices))


def _set(prices, symbol, day, **values):
    mask = (prices["symbol"] == symbol) & (prices["date"] == day)
    for key, value in values.items():
        prices.loc[mask, key] = value


def test_entry_next_open_and_exit_open_after_horizon() -> None:
    sessions = _sessions(date(2019, 1, 1), 80)
    prices = _prices(sessions)
    market = _market(prices, sessions)
    cube = build_cube(market, 5, len(sessions) - 1)
    r, t = market.panel.row["S03"], 10
    p = prices.set_index(["symbol", "date"])
    expected = p.loc[("S03", sessions[16]), "open"] / p.loc[("S03", sessions[11]), "open"] - 1
    assert cube.status[r, t] == FILLED
    assert cube.exit_index[r, t] == 16
    assert cube.gross[r, t] == pytest.approx(expected, rel=1e-9)


def test_before_real_opens_entry_and_exit_use_closes() -> None:
    sessions = _sessions(date(2016, 1, 1), 80)
    prices = _prices(sessions)
    market = _market(prices, sessions)
    cube = build_cube(market, 5, len(sessions) - 1)
    r, t = market.panel.row["S04"], 10
    p = prices.set_index(["symbol", "date"])
    expected = p.loc[("S04", sessions[16]), "close"] / p.loc[("S04", sessions[11]), "close"] - 1
    assert cube.gross[r, t] == pytest.approx(expected, rel=1e-9)
    assert not cube.entry_open_rule[t]


def test_locked_upper_circuit_entry_is_unfilled_and_counted_wrong() -> None:
    sessions = _sessions(date(2019, 1, 1), 80)
    prices = _prices(sessions)
    prev = float(prices[(prices.symbol == "S01") & (prices.date == sessions[10])]["close"].iloc[0])
    locked = prev * 1.10
    _set(prices, "S01", sessions[11], open=locked, high=locked, low=locked, close=locked)
    market = _market(prices, sessions)
    cube = build_cube(market, 5, len(sessions) - 1)
    r = market.panel.row["S01"]
    assert cube.status[r, 10] == UNFILLED
    assert not cube.correct[r, 10]
    graded = grade_calls(market, {5: cube}, pd.DataFrame({"call_id": [1], "symbol": ["S01"], "signal_date": [sessions[10]]}))
    assert graded["status"].iloc[0] == spec.STATUS_UNFILLED
    assert graded["failure_cause"].iloc[0] == "liquidity"


def test_blocked_sell_is_a_loss_even_when_the_price_rose() -> None:
    sessions = _sessions(date(2019, 1, 1), 80)
    prices = _prices(sessions)
    prices = prices[~((prices.symbol == "S02") & (prices.date == sessions[16]))].copy()
    later = prices["symbol"].eq("S02") & prices["date"].gt(sessions[16])
    prices.loc[later, ["open", "high", "low", "close"]] *= 1.05
    market = _market(prices, sessions)
    cube = build_cube(market, 5, len(sessions) - 1)
    r = market.panel.row["S02"]
    assert cube.status[r, 10] == BLOCKED
    assert cube.exit_index[r, 10] == 17
    assert not cube.correct[r, 10]


def test_hundred_calls_on_four_dates_count_as_about_four_observations() -> None:
    sessions = _sessions(date(2019, 1, 1), 400)
    index = {d: i for i, d in enumerate(sessions)}
    dates = [sessions[i] for i in (10, 110, 210, 310)]
    rng = np.random.default_rng(1)
    frame = pd.DataFrame(
        {
            "signal_date": np.repeat(dates, 25),
            "symbol": [f"X{i}" for i in range(100)],
            "status": spec.STATUS_FILLED,
            "correct": rng.random(100) < 0.7,
            "baseline_share": 0.4,
            "gross_return": 0.05,
            "universe_mean": 0.0,
            "nepse_return": 0.0,
            "probability": 0.7,
            "failure_cause": None,
            "score": None,
        }
    )
    metrics = cell_metrics(frame, 20, index, len(sessions), tests=1)
    assert metrics["independent_windows"] == 4
    assert metrics["distinct_dates"] == 4
    assert metrics["verdict"] == INSUFFICIENT


def test_a_ten_call_batch_can_never_pass() -> None:
    sessions = _sessions(date(2019, 1, 1), 400)
    index = {d: i for i, d in enumerate(sessions)}
    frame = pd.DataFrame(
        {
            "signal_date": [sessions[i * 30] for i in range(10)],
            "symbol": [f"X{i}" for i in range(10)],
            "status": spec.STATUS_FILLED,
            "correct": True,
            "baseline_share": 0.3,
            "gross_return": 0.2,
            "universe_mean": 0.0,
            "nepse_return": 0.0,
            "probability": 1.0,
            "failure_cause": None,
            "score": None,
        }
    )
    metrics = cell_metrics(frame, 5, index, len(sessions), tests=1)
    assert metrics["win_rate"] == 1.0
    assert metrics["verdict"] != "PASS"


def test_market_state_and_situations_use_only_data_up_to_t() -> None:
    sessions = _sessions(date(2019, 1, 1), 320)
    prices = _prices(sessions)
    cut = 260
    full = situation_matrix(_market(prices, sessions), _index(prices), RATES, set())
    changed = prices.copy()
    late = changed["date"] > sessions[cut]
    changed.loc[late, ["open", "high", "low", "close"]] *= np.random.default_rng(3).uniform(0.7, 1.3, late.sum())[:, None]
    changed.loc[late, "volume"] *= 9
    truncated = prices[prices["date"] <= sessions[cut]]
    for variant in (changed, truncated):
        other = situation_matrix(_market(variant, sessions), _index(variant), RATES, set())
        for name in spec.SITUATIONS:
            if name in ("pre_book_close",):
                continue
            assert np.array_equal(full[name][:, : cut + 1], other[name][:, : cut + 1]), name
    labels_full = market_state_labels(_index(prices), tuple(sessions))
    labels_cut = market_state_labels(_index(truncated), tuple(sessions))
    assert labels_full.iloc[: cut + 1].equals(labels_cut.iloc[: cut + 1])
    assert labels_full.iloc[: cut + 1].notna().sum() > 50


def test_lookahead_audit_catches_the_leaky_strategy_only() -> None:
    sessions = _sessions(date(2019, 1, 1), 160)
    prices = _prices(sessions)
    panel = es.build_panel(prices, NO_ACTIONS, SECTORS, sessions=sessions)
    audits = lookahead_audit(STRATEGIES, panel, prices, NO_ACTIONS, SECTORS, sample=8, first_index=30)
    assert audits["leaky_future_return"]["leaky"]
    assert not audits["baseline_random"]["leaky"]
    assert not audits["baseline_equal_weight"]["leaky"]
    assert not audits["baseline_momentum"]["leaky"]


@pytest.fixture
def ledger_schema():
    sqlalchemy = pytest.importorskip("sqlalchemy")
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


def test_ledger_rejects_update_delete_and_truncate(ledger_schema) -> None:
    from sqlalchemy import text

    engine, schema = ledger_schema
    with engine.begin() as connection:
        call_id = connection.execute(
            text(
                f"INSERT INTO {schema}.scorecard_calls (call_uid, mode, strategy, model_version, feature_hash, batch_id, "
                "symbol, signal_date, probability, situations) VALUES (:u, 'replay', 's', 'v1', :h, 'b', 'NABIL', "
                "'2020-01-05', 0.5, ARRAY['all']) RETURNING id"
            ),
            {"u": str(uuid.uuid4()), "h": "0" * 64},
        ).scalar_one()
        connection.execute(
            text(
                f"INSERT INTO {schema}.scorecard_grades (call_id, grade_version, horizon, horizon_class, status, correct) "
                "VALUES (:c, 'accuracy-v1', 5, 'short', 'filled', true)"
            ),
            {"c": call_id},
        )
    for statement in (
        f"UPDATE {schema}.scorecard_calls SET probability = 0.9",
        f"DELETE FROM {schema}.scorecard_calls",
        f"TRUNCATE {schema}.scorecard_calls CASCADE",
        f"UPDATE {schema}.scorecard_grades SET correct = false",
        f"DELETE FROM {schema}.scorecard_grades",
        f"TRUNCATE {schema}.scorecard_grades",
    ):
        with pytest.raises(Exception, match="append-only"):
            with engine.begin() as connection:
                connection.exec_driver_sql(statement)
    with engine.connect() as connection:
        assert connection.execute(text(f"SELECT count(*) FROM {schema}.scorecard_calls")).scalar_one() == 1
        assert connection.execute(text(f"SELECT probability FROM {schema}.scorecard_calls")).scalar_one() == pytest.approx(0.5)


def test_live_call_recorded_after_the_entry_open_is_rejected(ledger_schema) -> None:
    from sqlalchemy import text

    engine, schema = ledger_schema
    late = datetime(2020, 1, 6, 6, 0, tzinfo=timezone.utc)
    with pytest.raises(Exception):
        with engine.begin() as connection:
            connection.execute(
                text(
                    f"INSERT INTO {schema}.scorecard_calls (call_uid, mode, strategy, model_version, feature_hash, batch_id, "
                    "symbol, signal_date, probability, situations, created_at) VALUES (:u, 'live', 's', 'v1', :h, 'b', "
                    "'NABIL', '2020-01-05', 0.5, ARRAY['all'], :c)"
                ),
                {"u": str(uuid.uuid4()), "h": "0" * 64, "c": late},
            )
    early = datetime(2020, 1, 5, 12, 0, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            text(
                f"INSERT INTO {schema}.scorecard_calls (call_uid, mode, strategy, model_version, feature_hash, batch_id, "
                "symbol, signal_date, probability, situations, created_at) VALUES (:u, 'live', 's', 'v1', :h, 'b', "
                "'NABIL', '2020-01-05', 0.5, ARRAY['all'], :c)"
            ),
            {"u": str(uuid.uuid4()), "h": "0" * 64, "c": early},
        )


def test_invalid_symbols_are_rejected() -> None:
    assert not spec.valid_symbol("")
    assert not spec.valid_symbol(None)
    assert not spec.valid_symbol("NICAD 85/8")
    assert not spec.valid_symbol("NIFRAUR85/")
    assert spec.valid_symbol("NABIL")


def test_spread_is_not_reported_for_constant_scores() -> None:
    from src.scorecard.metrics import top_bottom_spread

    frame = pd.DataFrame(
        {"signal_date": [date(2020, 1, 1)] * 20, "symbol": [f"S{i:02d}" for i in range(20)], "score": 0.0,
         "gross_return": np.linspace(-0.1, 0.1, 20)}
    )
    assert top_bottom_spread(frame) is None
    frame["score"] = np.linspace(0, 1, 20)
    assert top_bottom_spread(frame) == pytest.approx(0.2 * (1 - 3 / 19), rel=1e-6)


def test_window_spanning_an_unresolved_step_is_excluded() -> None:
    from src.scorecard.grading import DATA_ERROR, unresolved_steps_mask

    sessions = _sessions(date(2019, 1, 1), 80)
    prices = _prices(sessions)
    later = prices["symbol"].eq("S05") & prices["date"].ge(sessions[14])
    prices.loc[later, ["open", "high", "low", "close"]] /= 1.3
    panel = es.build_panel(prices, NO_ACTIONS, SECTORS, sessions=sessions)
    mask, steps = unresolved_steps_mask(prices, NO_ACTIONS, panel)
    assert list(steps["symbol"]) == ["S05"]
    market = build_market(panel, NO_ACTIONS, _index(prices), unresolved=mask)
    cube = build_cube(market, 5, len(sessions) - 1)
    r = market.panel.row["S05"]
    assert cube.status[r, 10] == DATA_ERROR
    assert cube.status[r, 20] != DATA_ERROR
    assert cube.status[market.panel.row["S06"], 10] != DATA_ERROR
