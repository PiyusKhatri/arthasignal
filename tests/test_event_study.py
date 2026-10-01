from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.backtest import event_study as es


def _sessions(start: date, count: int) -> list[date]:
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _prices(sessions, symbols=("AAA", "BBB", "CCC", "DDD"), seed=3):
    rng = np.random.default_rng(seed)
    rows = []
    for symbol in symbols:
        close = 200.0
        for day in sessions:
            prev = close
            close = prev * float(np.exp(rng.normal(0, 0.015)))
            rows.append((symbol, day, prev, max(prev, close) * 1.005, min(prev, close) * 0.995, close, 1000.0, 1000.0 * close))
    return pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "volume", "turnover"])


SECTORS = {"AAA": "Banks", "BBB": "Banks", "CCC": "Hydro", "DDD": "Hydro"}
NO_ACTIONS = pd.DataFrame(columns=["symbol", "action_date", "action_type", "ratio_or_amount"])


def _set(prices, symbol, day, **values):
    mask = (prices["symbol"] == symbol) & (prices["date"] == day)
    for key, value in values.items():
        prices.loc[mask, key] = value


def test_bonus_right_and_dividend_are_adjusted_explicitly() -> None:
    sessions = _sessions(date(2019, 1, 1), 30)
    prices = _prices(sessions)
    prices.loc[prices["symbol"] == "AAA", ["open", "high", "low", "close"]] = 200.0
    ex = sessions[10]
    after = prices["symbol"].eq("AAA") & prices["date"].ge(ex)
    prices.loc[after, ["open", "high", "low", "close"]] = 200.0 / 1.25
    right_ex = sessions[20]
    after_right = prices["symbol"].eq("AAA") & prices["date"].ge(right_ex)
    terp = (160.0 + 0.5 * 100.0) / 1.5
    prices.loc[after_right, ["open", "high", "low", "close"]] = terp
    actions = pd.DataFrame(
        {
            "symbol": ["AAA", "AAA", "AAA"],
            "action_date": [ex, ex, right_ex - timedelta(days=0)],
            "action_type": ["BONUS", "DIVIDEND", "RIGHT"],
            "ratio_or_amount": [25.0, 5.0, 50.0],
        }
    )
    panel = es.build_panel(prices, actions, SECTORS)
    r = panel.row["AAA"]
    assert panel.total_return[r, 10] == pytest.approx(5.0 * 0.95 / 200.0)
    assert panel.total_return[r, 20] == pytest.approx(0.0, abs=1e-12)
    assert not panel.corrupt[r].any()


def test_unrecorded_split_is_flagged_and_excluded() -> None:
    sessions = _sessions(date(2019, 1, 1), 30)
    prices = _prices(sessions)
    later = prices["symbol"].eq("BBB") & prices["date"].ge(sessions[15])
    prices.loc[later, ["open", "high", "low", "close"]] /= 2
    panel = es.build_panel(prices, NO_ACTIONS, SECTORS)
    r = panel.row["BBB"]
    assert panel.corrupt[r, 15]
    assert np.isnan(panel.one_session_return[r, 15])
    assert es.stock_cumulative(panel, r, 10, 20) is None


def test_no_buy_on_locked_upper_circuit_and_entry_strictly_after_knowledge() -> None:
    sessions = _sessions(date(2019, 1, 1), 60)
    prices = _prices(sessions)
    prev = float(prices[(prices.symbol == "AAA") & (prices.date == sessions[10])]["close"].iloc[0])
    locked = prev * 1.10
    _set(prices, "AAA", sessions[11], open=locked, high=locked, low=locked, close=locked)
    panel = es.build_panel(prices, NO_ACTIONS, SECTORS)
    universe = es.benchmark_returns(panel)
    trade, outcome = es.simulate_trade(panel, "AAA", 10, 20, universe, universe)
    assert outcome == "filled"
    assert trade.entry_date == sessions[12]
    assert trade.entry_delay == 2
    assert trade.entry_date > trade.knowledge_date


def test_blocked_sell_on_thin_or_locked_down_session_is_deferred() -> None:
    sessions = _sessions(date(2019, 1, 1), 60)
    prices = _prices(sessions)
    prices = prices[~((prices.symbol == "CCC") & (prices.date == sessions[30]))].copy()
    prev = float(prices[(prices.symbol == "CCC") & (prices.date == sessions[29])]["close"].iloc[0])
    down = prev * 0.90
    _set(prices, "CCC", sessions[31], open=down, high=down, low=down, close=down)
    later = prices["symbol"].eq("CCC") & prices["date"].gt(sessions[31])
    prices.loc[later, ["open", "high", "low", "close"]] *= down / float(prices.loc[later, "open"].iloc[0])
    panel = es.build_panel(prices, NO_ACTIONS, SECTORS, sessions=sessions)
    universe = es.benchmark_returns(panel)
    trade, outcome = es.simulate_trade(panel, "CCC", 10, 20, universe, universe)
    assert outcome == "filled"
    assert trade.exit_date == sessions[32]
    assert trade.exit_status == es.EXIT_DELAYED


def test_before_real_opens_entry_is_at_next_close() -> None:
    sessions = _sessions(date(2016, 1, 1), 40)
    panel = es.build_panel(_prices(sessions), NO_ACTIONS, SECTORS)
    universe = es.benchmark_returns(panel)
    trade, _ = es.simulate_trade(panel, "AAA", 5, 20, universe, universe)
    assert trade.entry_rule == es.ENTRY_CLOSE
    assert trade.exit_index - trade.entry_index == 20


def test_trade_never_reads_past_last_index() -> None:
    sessions = _sessions(date(2019, 1, 1), 60)
    panel = es.build_panel(_prices(sessions), NO_ACTIONS, SECTORS)
    universe = es.benchmark_returns(panel)
    trade, outcome = es.simulate_trade(panel, "AAA", 30, 20, universe, universe, last_index=45)
    assert trade is None and outcome == "exit_after_window"


def _events(sessions):
    return pd.DataFrame({"symbol": ["AAA", "CCC", "BBB"], "event_date": [sessions[30], sessions[35], sessions[40]]})


def test_abnormal_path_up_to_k_ignores_data_after_anchor_plus_k() -> None:
    sessions = _sessions(date(2019, 1, 1), 130)
    prices = _prices(sessions)
    events = _events(sessions)
    base = es.event_paths(es.build_panel(prices, NO_ACTIONS, SECTORS, sessions=sessions), events)
    assert base["status"].eq("ok").all()
    for k in (-1, 0, 5, 30):
        for position, event in enumerate(events.itertuples()):
            cutoff = sessions[sessions.index(event.event_date) + k]
            rng = np.random.default_rng(k + 50 + position)
            changed = prices.copy()
            late = changed["date"] > cutoff
            changed.loc[late, ["open", "high", "low", "close"]] = changed.loc[late, ["open", "high", "low", "close"]].mul(
                rng.uniform(0.95, 1.05, late.sum()), axis=0
            )
            truncated = prices[prices["date"] <= cutoff]
            for variant in (changed, truncated):
                other = es.event_paths(es.build_panel(variant, NO_ACTIONS, SECTORS, sessions=sessions), events)
                for j in range(-10, k + 1):
                    for prefix in ("u", "s"):
                        assert other[f"{prefix}{j}"].iloc[position] == pytest.approx(
                            base[f"{prefix}{j}"].iloc[position], abs=1e-12
                        )


def test_trade_result_ignores_prices_after_exit() -> None:
    sessions = _sessions(date(2019, 1, 1), 100)
    prices = _prices(sessions)
    panel = es.build_panel(prices, NO_ACTIONS, SECTORS)
    universe = es.benchmark_returns(panel)
    trade, _ = es.simulate_trade(panel, "AAA", 20, 20, universe, universe)
    changed = prices.copy()
    late = changed["date"] > trade.exit_date
    changed.loc[late, ["open", "high", "low", "close"]] *= 1.07
    panel2 = es.build_panel(changed, NO_ACTIONS, SECTORS)
    universe2 = es.benchmark_returns(panel2)
    trade2, _ = es.simulate_trade(panel2, "AAA", 20, 20, universe2, universe2)
    assert trade2 == trade


def test_counter_tracks_tests_and_refuses_unplanned_ones() -> None:
    counter = es.TestCounter("family", planned=2)
    assert counter.alpha == pytest.approx(0.025)
    counter.record("a")
    counter.record("b")
    with pytest.raises(ValueError):
        counter.record("a")
    with pytest.raises(ValueError):
        counter.record("c")


def test_summary_uses_date_clusters() -> None:
    frame = pd.DataFrame(
        {
            "entry_date": [date(2020, 1, 1)] * 3 + [date(2020, 1, 2)] * 3 + [date(2020, 1, 3)] * 3,
            "entry_index": [0] * 3 + [1] * 3 + [25] * 3,
            "gross_return": [0.05] * 9,
            "universe_return": [0.01] * 9,
            "exit_status": ["on_time"] * 9,
            "entry_rule": ["next_open"] * 9,
        }
    )
    summary = es.summarize_trades(frame, 0.05)
    assert summary["excess_1.0%"]["by_date"]["clusters"] == 3
    assert summary["excess_1.0%"]["by_20_session_block"]["clusters"] == 2
    assert summary["excess_1.0%"]["by_date"]["mean_pct"] == pytest.approx(3.0)
