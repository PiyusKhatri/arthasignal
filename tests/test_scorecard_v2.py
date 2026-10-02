from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from src.backtest import event_study as es
from src.scorecard import spec, v2
from src.scorecard.grading import FILLED, build_cube, build_market


def _sessions(start: date, count: int) -> list[date]:
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


SESSIONS = _sessions(date(2018, 3, 1), 1200)
INDEX = {d: i for i, d in enumerate(SESSIONS)}


def _cell(win: float, baseline: float, probability=None, n_dates: int = 200, per_date: int = 5, seed: int = 0, gross: float = 0.05):
    rng = np.random.default_rng(seed)
    dates = [SESSIONS[i * 6] for i in range(n_dates)]
    rows = []
    for d in dates:
        for k in range(per_date):
            rows.append(
                {
                    "signal_date": d,
                    "symbol": f"X{k}",
                    "status": spec.STATUS_FILLED,
                    "correct": rng.random() < win,
                    "baseline_share": baseline,
                    "gross_return": gross,
                    "universe_mean": 0.01,
                    "nepse_return": 0.0,
                    "probability": probability,
                    "failure_cause": None,
                    "score": None,
                }
            )
    return pd.DataFrame(rows)


def test_gate_is_edge_over_baseline_not_absolute_win_rate() -> None:
    strong_edge = v2.cell_metrics_v2(_cell(0.45, 0.30), 5, INDEX, len(SESSIONS), tests=104)
    assert strong_edge["verdict"] == "PASS"
    high_but_no_edge = v2.cell_metrics_v2(_cell(0.66, 0.62, seed=1), 5, INDEX, len(SESSIONS), tests=104)
    assert high_but_no_edge["verdict"] == "NO EVIDENCE"
    assert not high_but_no_edge["gates"]["edge_8_points"]


def test_excess_expectancy_must_beat_the_universe() -> None:
    cell = v2.cell_metrics_v2(_cell(0.45, 0.30, gross=0.015), 5, INDEX, len(SESSIONS), tests=104)
    assert cell["gates"]["expectancy_all_costs"] is False or cell["excess_expectancy_1pct"] <= 0
    assert cell["verdict"] == "NO EVIDENCE"


def test_calibration_is_judged_against_the_real_baseline() -> None:
    coin_flip = v2.cell_metrics_v2(_cell(0.30, 0.30, probability=0.5), 5, INDEX, len(SESSIONS), tests=104)
    assert not coin_flip["gates"]["calibration"]
    assert coin_flip["calibration"]["brier"] > coin_flip["calibration"]["brier_baseline_forecast"]
    honest = v2.cell_metrics_v2(_cell(0.45, 0.30, probability=0.45, seed=3), 5, INDEX, len(SESSIONS), tests=104)
    assert honest["gates"]["calibration"]
    unclaimed = v2.cell_metrics_v2(_cell(0.45, 0.30, probability=None), 5, INDEX, len(SESSIONS), tests=104)
    assert unclaimed["calibration"]["claimed"] is False and unclaimed["gates"]["calibration"]


def test_long_horizon_cells_cannot_reach_the_window_minimum() -> None:
    support = v2.supportable_horizons()
    assert [h for h, s in support.items() if not s["can_ever_support_a_claim"]] == [120, 160, 240]
    dense = _cell(0.60, 0.30, n_dates=190, per_date=2)
    cell = v2.cell_metrics_v2(dense, 120, INDEX, len(SESSIONS), tests=104)
    assert cell["independent_windows"] < v2.MIN_WINDOWS[120]
    assert cell["verdict"] == "INSUFFICIENT SAMPLE"
    short = v2.cell_metrics_v2(_cell(0.60, 0.30, n_dates=30, per_date=10), 5, INDEX, len(SESSIONS), tests=104)
    assert short["verdict"] == "INSUFFICIENT SAMPLE"


def _graded_for_monitor(probability: float, baseline: float, win: float) -> pd.DataFrame:
    rng = np.random.default_rng(4)
    rows = []
    for i in range(600):
        d = SESSIONS[i]
        rows.append(
            {
                "horizon": 5, "status": spec.STATUS_FILLED, "exit_date": SESSIONS[i + 6], "correct": rng.random() < win,
                "baseline_share": baseline, "gross_return": 0.02, "universe_mean": 0.01, "probability": probability,
                "signal_date": d,
            }
        )
    return pd.DataFrame(rows)


def test_brier_kill_rule_fires_for_a_miscalibrated_claim_and_not_for_a_calibrated_one() -> None:
    bad = v2.rolling_monitor_v2(_graded_for_monitor(0.5, 0.30, 0.30), 5, SESSIONS)
    assert bad["kill_brier"].any()
    good = v2.rolling_monitor_v2(_graded_for_monitor(0.30, 0.30, 0.30), 5, SESSIONS)
    assert not good["kill_brier"].all()
    assert good["kill_brier"].mean() < bad["kill_brier"].mean()


def _market_with(prices, sessions, actions=None):
    actions = actions if actions is not None else pd.DataFrame(columns=["symbol", "action_date", "action_type", "ratio_or_amount"])
    sectors = {s: ("A" if s < "S05" else "B") for s in prices["symbol"].unique()}
    panel = es.build_panel(prices, actions, sectors, sessions=sessions)
    index = prices.groupby("date")["close"].mean().rename("close").reset_index()
    index["open"] = index["close"]
    return build_market(panel, actions, index)


def _prices(sessions, drift, spikes=()):
    rows = []
    for k in range(10):
        symbol = f"S{k:02d}"
        close = 100.0
        for i, day in enumerate(sessions):
            prev = close
            close = prev * (1 + drift.get((symbol, i), 0.0) + 0.0005 * ((k + i) % 3 - 1))
            volume = 10000.0 if (symbol, i) in spikes else 1000.0
            rows.append((symbol, day, prev, max(prev, close), min(prev, close), close, volume, volume * close))
    return pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "volume", "turnover"])


def _causes(spikes=()):
    sessions = _sessions(date(2019, 1, 1), 400)
    drift = {}
    for k in range(10):
        for i in range(20, 260):
            drift[(f"S{k:02d}", i)] = -0.0005
    drops = list(range(80, 260, 7))
    for i in drops:
        drift[("S01", i)] = -0.015
    prices = _prices(sessions, drift, {("S01", i) for i in drops} if spikes else ())
    market = _market_with(prices, sessions)
    cube = build_cube(market, 240, len(sessions) - 1)
    rows = np.array([market.panel.row["S01"], market.panel.row["S03"]])
    cols = np.array([10, 10])
    assert cube.status[rows[0], 10] == FILLED
    return v2.failure_causes_v2(market, cube, rows, cols, v2.cumulative_event_returns(market))


def test_failure_causes_stay_informative_at_long_horizons() -> None:
    plain = _causes()
    assert plain[0] == "model"
    assert plain[1] in ("market", "model", None)
    with_news = _causes(spikes=True)
    assert with_news[0] == "news"


def test_zero_edge_never_clears_the_lower_bound_by_rounding() -> None:
    frame = _cell(0.3, 0.3)
    frame["correct"] = [True, False, False, True, False] * (len(frame) // 5)
    frame["baseline_share"] = frame.groupby("signal_date")["correct"].transform("mean") + 1e-17
    cell = v2.cell_metrics_v2(frame, 40, INDEX, len(SESSIONS), tests=104)
    assert not cell["gates"]["edge_lower_bound_above_zero"]
