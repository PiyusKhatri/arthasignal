from __future__ import annotations

from datetime import date

import pandas as pd

from src.backtest import event_study as es
from src.league import bots as league_bots
from src.league import diagnose
from tests.test_league import NO_ACTIONS, SECTORS, _index, _prices, _sessions, _setup


def _state(prices, panel, actions=NO_ACTIONS):
    return {"inputs": {"index": _index(prices), "prices": prices}, "panel": panel, "actions": actions, "mergers": set()}


def test_funnel_matches_every_bot_selector_on_a_bull_panel() -> None:
    sessions, prices, panel = _setup(trend=0.0015)
    report = diagnose.diagnose(_state(prices, panel), set())
    league = league_bots.League(_index(prices), NO_ACTIONS, set())
    t = len(panel.sessions) - 1
    assert report["eligibility"]["matches_bot_code"]
    assert report["eligibility"]["stages"][0]["passed"] == len(SECTORS)
    for name, entry in report["bots"].items():
        assert entry["picks"] == len(league.selector(name)(panel, t)), name
    assert report["bots"]["bot_market_timer"]["matches_bot_code"]
    assert report["bots"]["bot_avoid_e2e4"]["matches_bot_code"]


def test_bear_market_zeroes_the_gated_bots_and_names_the_failed_condition() -> None:
    sessions, prices, panel = _setup(trend=-0.004)
    report = diagnose.diagnose(_state(prices, panel), set())
    verdict = diagnose.verdicts(report)
    assert report["market_state"]["state"] == "market_bear"
    momentum = report["bots"]["bot_momentum"]
    assert momentum["written_would_be"] == 0 and momentum["picks_if_state_gate_were_open"]
    assert "state is not market_bear" in verdict["bot_momentum"] and "state is not market_bear" in verdict["bot_new_listing"]
    assert "market_bull" in verdict["bot_market_timer"] and "market timer on" in verdict["bot_combined"]
    assert report["bots"]["bot_ranker_spec"]["written_would_be"] == 10


def test_avoid_counts_bonus_book_closes_in_the_window_and_flags_an_empty_window() -> None:
    sessions, prices, panel = _setup(trend=0.0005)
    t = len(panel.sessions) - 1
    empty = diagnose.diagnose(_state(prices, panel), set())
    assert empty["bots"]["bot_avoid_e2e4"]["e2_bonus_book_close"]["bonus_rows_in_window"] == 0
    assert "no bonus book close in the window" in diagnose.verdicts(empty)["bot_avoid_e2e4"]
    actions = pd.DataFrame({"symbol": ["S01", "S02"], "action_date": [sessions[t - 3], sessions[t - 40]],
                            "action_type": ["BONUS", "BONUS"], "ratio_or_amount": [10.0, 10.0]})
    report = diagnose.diagnose(_state(prices, panel, actions), set())
    avoid = report["bots"]["bot_avoid_e2e4"]
    assert avoid["e2_bonus_book_close"]["bonus_rows_in_window"] == 1 and avoid["e2_bonus_book_close"]["hits"] == ["S01"]
    assert avoid["matches_bot_code"] and avoid["written_would_be"] == 1
    blocked = diagnose.diagnose(_state(prices, panel, actions), {"S01"})["bots"]["bot_avoid_e2e4"]
    assert blocked["removed_by_quarantine_or_exclusion"] == ["S01"] and blocked["written_would_be"] == 0


def test_thin_backfilled_sessions_are_reported_with_the_symbols_they_cost() -> None:
    sessions = _sessions(date(2019, 1, 1), 330)
    prices = _prices(sessions, 0.0015)
    gap = set(sessions[-15:-4])
    prices = prices[~((prices["symbol"].isin(["S00", "S01", "S02"])) & prices["date"].isin(gap))]
    panel = es.build_panel(prices, NO_ACTIONS, SECTORS, sessions=sessions)
    report = diagnose.diagnose(_state(prices, panel), set())
    assert len(report["data"]["thin_sessions_last_60"]) == 11
    stage = next(s for s in report["eligibility"]["stages"] if s["filter"].startswith("traded on at least"))
    assert stage["dropped"] == 3
    assert report["eligibility"]["short_history_symbols_that_only_miss_thin_sessions"] == ["S00", "S01", "S02"]
