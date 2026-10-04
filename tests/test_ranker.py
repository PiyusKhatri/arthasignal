from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd

from src.backtest import event_study as es
from src.ranker import features as feat
from src.ranker import meta_spec, spec
from src.ranker import train


def _sessions(start: date, count: int) -> list[date]:
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def test_spec_counts_sixteen_ranker_trials_and_four_meta_trials() -> None:
    assert len(spec.trials()) == 16 and len({(t["strategy"], str(t["grid_point"])) for t in spec.trials()}) == 16
    assert len(meta_spec.trials()) == 4
    assert spec.FOLDS[0][0] == spec.FIRST_TEST_DAY and spec.FOLDS[-1][1] == spec.WINDOW_END
    assert set(spec.FEATURES) == set(spec.STOCK_FEATURES) | set(spec.MARKET_FEATURES)


def test_rank_transform_ranks_stock_features_within_each_date_only() -> None:
    raw = {name: np.array([[1.0, 5.0], [3.0, np.nan], [2.0, 4.0]], dtype=np.float32) for name in spec.FEATURES}
    ranked = feat.rank_transform(raw)
    assert np.allclose(ranked["ret_20"][:, 0], [1 / 3, 1.0, 2 / 3])
    assert np.isnan(ranked["ret_20"][1, 1]) and np.allclose(ranked["ret_20"][[0, 2], 1], [1.0, 0.5])
    assert np.array_equal(ranked["nepse_ret_20"], raw["nepse_ret_20"], equal_nan=True)


def test_since_counts_sessions_after_past_events_only() -> None:
    out = feat._since({0: [3]}, 1, 6)
    assert out[0].tolist() == [250, 250, 250, 0, 1, 2]


def test_labels_are_within_date_ranks_of_filled_excess_returns() -> None:
    cube = SimpleNamespace(status=np.array([[1, 1], [1, 2], [1, 1]]), gross=np.array([[0.1, 0.0], [0.3, 0.0], [0.2, 0.0]]),
                           universe_mean=np.array([0.0, 0.0]), exit_index=np.array([[5, 6], [5, 6], [5, 6]]))
    ctx = {"cubes": {5: cube}}
    frame = pd.DataFrame({"r": [0, 1, 2], "t": [0, 0, 0]})
    old = spec.MIN_CROSS_SECTION
    spec.MIN_CROSS_SECTION = 2
    try:
        lab = train.labels(ctx, frame, 5)
    finally:
        spec.MIN_CROSS_SECTION = old
    assert np.allclose(lab["label"], [1 / 3, 1.0, 2 / 3])


def test_fold_bounds_follow_the_preregistered_calendar_blocks() -> None:
    sessions = _sessions(date(2018, 1, 1), 1900)
    bounds = train.fold_bounds([d for d in sessions if d <= spec.WINDOW_END])
    assert len(bounds) == 7
    assert sessions[bounds[0][0]] >= date(2018, 2, 18) and sessions[bounds[1][0]].year == 2019


def test_feature_compute_has_no_lookahead_on_synthetic_data() -> None:
    sessions = _sessions(date(2019, 1, 1), 300)
    rng = np.random.default_rng(5)
    rows = []
    for k in range(12):
        close = 100.0
        for day in sessions:
            prev, close = close, close * float(np.exp(rng.normal(0.0005, 0.02)))
            rows.append((f"S{k:02d}", day, prev, max(prev, close), min(prev, close), close, 1e5, 1e5 * close))
    prices = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "volume", "turnover"])
    actions = pd.DataFrame({"symbol": ["S01"], "action_date": [sessions[250]], "action_type": ["BONUS"], "ratio_or_amount": [10.0]})
    index = prices.groupby("date")["close"].mean().rename("close").reset_index()
    inputs = {"prices": prices, "sessions": pd.DataFrame({"date": sessions}), "actions": actions, "index": index,
              "rates": pd.DataFrame(columns=["fiscal_year", "month", "treasury_bill_rate"]),
              "companies": pd.DataFrame({"symbol": [f"S{k:02d}" for k in range(12)], "sector": ["A", "B"] * 6})}
    extras = {"reports": pd.DataFrame(columns=["symbol", "fiscal_year", "quarter", "net_profit", "published_date", "ss_date", "ml_date", "available_date"]),
              "declarations": pd.DataFrame({"symbol": ["S02"], "announcement_date": [sessions[260]], "bonus": [12.0]}),
              "broker": pd.DataFrame({"symbol": ["S03"], "date": [sessions[270]], "h1": [0.5], "h2": [0.1], "h3": [0.2], "h4": [0.3], "h5": [0.4]})}
    sectors = dict(zip(inputs["companies"]["symbol"], inputs["companies"]["sector"]))
    full_panel = es.build_panel(prices, actions, sectors, sessions=sessions)
    full = feat.compute(full_panel, inputs, extras)
    t = 255
    cut_inputs, cut_extras = feat.truncate(inputs, extras, sessions[t])
    cut_panel = es.build_panel(cut_inputs["prices"], cut_inputs["actions"], sectors, sessions=sessions[: t + 1])
    cut = feat.compute(cut_panel, cut_inputs, cut_extras)
    for name in spec.FEATURES:
        assert np.allclose(full[name][:, t], cut[name][:, t], equal_nan=True, rtol=1e-4), name
    assert full["since_bonus"][full_panel.row["S01"], 252] == 2
    assert full["broker_h1"][full_panel.row["S03"], 270] == np.float32(0.5) and np.isnan(full["broker_h1"][full_panel.row["S03"], 269])
