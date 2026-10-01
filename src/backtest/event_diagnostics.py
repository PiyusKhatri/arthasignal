from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.backtest import event_eval as ev
from src.backtest import event_spec as spec
from src.backtest import event_study as es
from src.backtest import event_tables as et
from src.backtest.event_data import load_inputs, load_panel

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT = Path("docs/event_diagnostics_post_hoc.json")


def wealth_matrix(panel: es.Panel) -> tuple[np.ndarray, np.ndarray]:
    returns = np.nan_to_num(panel.total_return, nan=0.0)
    returns[panel.corrupt] = np.nan
    wealth = np.cumprod(1.0 + np.nan_to_num(returns, nan=0.0), axis=1)
    poisoned = np.cumsum(panel.corrupt, axis=1)
    return wealth, poisoned


def listed_mask(panel: es.Panel) -> np.ndarray:
    traded = ~np.isnan(panel.close)
    return np.cumsum(traded, axis=1) > 0


def buy_hold_universe(panel: es.Panel, wealth: np.ndarray, poisoned: np.ndarray, listed: np.ndarray, start: int, end: int) -> float:
    members = listed[:, start] & ~np.isnan(panel.close[:, start]) & (poisoned[:, end] == poisoned[:, start])
    if not members.any():
        return float("nan")
    return float(np.mean(wealth[members, end] / wealth[members, start] - 1.0))


def benchmark_bias(panel: es.Panel, horizon: int = 20) -> dict[str, Any]:
    wealth, poisoned = wealth_matrix(panel)
    listed = listed_mask(panel)
    daily = es.benchmark_returns(panel)
    rebalanced, held = [], []
    for start in range(0, len(panel.sessions) - horizon, 5):
        end = start + horizon
        rebalanced.append(float(np.prod(1 + daily[start + 1 : end + 1]) - 1))
        held.append(buy_hold_universe(panel, wealth, poisoned, listed, start, end))
    rebalanced, held = np.array(rebalanced), np.array(held)
    return {
        "horizon_sessions": horizon,
        "start_dates": int(len(rebalanced)),
        "mean_daily_rebalanced_pct": round(float(np.nanmean(rebalanced)) * 100, 3),
        "mean_buy_and_hold_pct": round(float(np.nanmean(held)) * 100, 3),
        "mean_bias_pct": round(float(np.nanmean(rebalanced - held)) * 100, 3),
    }


def run(output: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    inputs = load_inputs(spec.DEVELOPMENT_END)
    panel = load_panel(inputs)
    universe = es.benchmark_returns(panel)
    sectors = es.sector_benchmarks(panel)
    nepse = ev._nepse_daily(panel, inputs["index"])
    first = ev._first_index(panel)
    actions = inputs["actions"][inputs["actions"]["action_type"].isin(["BONUS", "DIVIDEND", "RIGHT"])]
    listings = et.new_listing_events(panel, et._merger_symbols(inputs))
    up_close, _, _, _ = et.circuit_flags(panel)
    ends = et.streak_end_events(et.circuit_streak_events(panel, listings), panel)
    volume = et.volume_anomaly_events(panel, actions, listings)
    sets = {
        "E1": ev.book_close_set(panel, actions, -16),
        "E2": ev.book_close_set(panel, actions, 0),
        "E4": ev.streak_end_set(ends),
        "E5": ev.volume_set(volume),
    }
    sets["E7"], sets["E8"] = sets["E5"], sets["E5"]
    wealth, poisoned = wealth_matrix(panel)
    listed = listed_mask(panel)
    report: dict[str, Any] = {
        "status": "post-hoc diagnostic written after the registered results were seen; not evidence and not a gate",
        "benchmark_bias_20": benchmark_bias(panel, 20),
        "benchmark_bias_60": benchmark_bias(panel, 60),
        "hypotheses": {},
    }
    for hypothesis_id, events in sets.items():
        hypothesis = spec.HYPOTHESES[hypothesis_id]
        trades, _ = ev.run_trades(panel, events, hypothesis["hold_sessions"], hypothesis["seasoned"], first, universe, sectors, nepse)
        starts = np.where(trades["entry_rule"] == es.ENTRY_OPEN, trades["entry_index"] - 1, trades["entry_index"])
        trades["universe_buy_hold"] = [
            buy_hold_universe(panel, wealth, poisoned, listed, int(s), int(x)) for s, x in zip(starts, trades["exit_index"])
        ]
        trades["abnormal_buy_hold"] = trades["gross_return"] - trades["universe_buy_hold"]
        trades["vs_nepse"] = trades["gross_return"] - trades["nepse_return"]
        cost = spec.PRIMARY_COST if hypothesis["direction"] == "long" else 0.0
        trades["primary_buy_hold"] = trades["abnormal_buy_hold"] - cost
        alpha = spec.BASE_ALPHA / len(spec.HYPOTHESES)
        trades["fold"] = ev._fold(trades["entry_index"], len(panel.sessions))
        report["hypotheses"][hypothesis_id] = {
            "trades": int(len(trades)),
            "mean_gross_pct": round(float(trades["gross_return"].mean()) * 100, 3),
            "mean_registered_universe_pct": round(float(trades["universe_return"].mean()) * 100, 3),
            "mean_buy_hold_universe_pct": round(float(trades["universe_buy_hold"].mean()) * 100, 3),
            "mean_nepse_pct": round(float(trades["nepse_return"].mean()) * 100, 3),
            "share_gross_negative_pct": round(float((trades["gross_return"] < 0).mean()) * 100, 1),
            "primary_vs_buy_hold_universe": es.clustered_intervals(trades, "primary_buy_hold", alpha),
            "gross_vs_nepse": es.clustered_intervals(trades, "vs_nepse", alpha),
            "fold_mean_vs_buy_hold_pct": {
                int(f): round(float(g["primary_buy_hold"].mean()) * 100, 3) for f, g in trades.groupby("fold")
            },
        }
    output.write_text(json.dumps(report, indent=2, default=str))
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(run(args.output), indent=2, default=str))


if __name__ == "__main__":
    main()
