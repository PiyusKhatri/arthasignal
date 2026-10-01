from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from src.backtest import event_eval as ev


def _trades(abnormal: float, noise: float, n_per_fold: int = 40, sessions: int = 800):
    rng = np.random.default_rng(2)
    rows = []
    for fold in range(4):
        for j in range(n_per_fold):
            index = fold * sessions // 4 + j * 3
            day = date(2019, 1, 1) + timedelta(days=index)
            gross = 0.02 + abnormal + rng.normal(0, noise)
            rows.append(
                {"symbol": f"S{j}", "entry_index": index, "entry_date": day, "exit_date": day + timedelta(days=30),
                 "gross_return": gross, "universe_return": 0.02, "sector_return": 0.02, "nepse_return": 0.0,
                 "exit_status": "on_time", "entry_rule": "next_open", "entry_delay": 1}
            )
    return pd.DataFrame(rows), sessions


def test_long_gates_require_clear_edge_after_costs() -> None:
    strong, n = _trades(0.05, 0.002)
    assert ev.evaluate_events(strong, "long", n, 0.00625, 0.001)["passes"]
    flat, n = _trades(0.0, 0.03)
    result = ev.evaluate_events(flat, "long", n, 0.00625, 0.001)
    assert not result["gates"]["G1"] and not result["passes"]


def test_avoid_gates_need_a_loss_bigger_than_costs() -> None:
    big, n = _trades(-0.03, 0.002)
    assert ev.evaluate_events(big, "avoid", n, 0.00625, 0.001)["passes"]
    small, n = _trades(-0.005, 0.0005)
    result = ev.evaluate_events(small, "avoid", n, 0.00625, 0.001)
    assert result["gates"]["G1"] and not result["gates"]["G2"] and not result["passes"]


def test_market_rule_trades_on_the_next_session_and_charges_switches() -> None:
    days = 260
    state = pd.DataFrame(
        {
            "date": [date(2016, 1, 1) + timedelta(days=i) for i in range(days)],
            "nepse_sma_200": [np.nan] * 10 + [1.0] * (days - 10),
            "trend_up": [np.nan] * 10 + [1.0] * 100 + [0.0] * (days - 110),
            "breadth_above_sma50": [0.6] * days,
            "tbill_rate_known": [np.nan] * days,
        }
    )
    instrument = np.full(days, 0.001)
    instrument[111] = -0.5
    strategy, benchmark, position = ev.market_rule_returns(state, instrument, 0.01)
    offset = 11
    assert position[110 - offset]
    assert not position[111 - offset]
    assert strategy[111 - offset] == -0.005
    assert benchmark[111 - offset] == -0.5
