from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import date
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd

from src.backtest import event_spec as spec
from src.backtest import event_study as es
from src.backtest import event_tables as et
from src.backtest.config import load_holdout_config
from src.backtest.event_data import load_inputs, load_panel
from src.backtest.holdout import assert_development_only, partition_rows
from src.backtest.ledger import variant_fingerprint
from src.backtest.types import Label, Row

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).with_name("event_config.json")
DEFAULT_OUTPUT = Path("docs/event_results.json")


def _first_index(panel: es.Panel) -> dict[str, int]:
    return et.first_price_index(panel)


def eligible(panel: es.Panel, symbol: str, knowledge: int, seasoned: bool, first: dict[str, int]) -> bool:
    r = panel.row.get(symbol)
    if r is None or knowledge < 0:
        return False
    lo = max(0, knowledge - spec.LIQUIDITY_LOOKBACK + 1)
    if int(np.sum(~np.isnan(panel.close[r, lo : knowledge + 1]))) < spec.LIQUIDITY_MIN_TRADED:
        return False
    if seasoned:
        start = first.get(symbol)
        if start is None:
            return False
        if panel.sessions[start] > et.LISTING_VISIBLE_AFTER and knowledge - start < spec.SEASONED_SESSIONS:
            return False
    return True


def book_close_set(panel: es.Panel, actions: pd.DataFrame, offset: int) -> pd.DataFrame:
    books = et.book_close_events(panel, actions)
    books = books[books["bonus_pct"].notna()].copy()
    books["knowledge_index"] = books["ex_index"] + offset
    return books[["symbol", "knowledge_index"]]


def new_listing_set(panel: es.Panel, listings: pd.DataFrame, up_close: np.ndarray) -> pd.DataFrame:
    records = []
    limit = spec.HYPOTHESES["E3"]["max_sessions_after_listing"]
    for symbol, first in zip(listings["symbol"], listings["listing_index"]):
        r = panel.row[symbol]
        traded = np.flatnonzero(~np.isnan(panel.close[r]))
        for c in traded[1:]:
            if c - first > limit:
                break
            if not up_close[r, c]:
                records.append({"symbol": symbol, "knowledge_index": int(c)})
                break
    return pd.DataFrame.from_records(records, columns=["symbol", "knowledge_index"])


def streak_end_set(ends: pd.DataFrame) -> pd.DataFrame:
    chosen = ends[(ends["direction"] == "up") & (ends["streak_length"] >= spec.HYPOTHESES["E4"]["min_streak"]) & ~ends["new_listing_window"]]
    return chosen.rename(columns={"session_index": "knowledge_index"})[["symbol", "knowledge_index"]]


def volume_set(volume: pd.DataFrame) -> pd.DataFrame:
    chosen = volume[volume["eligible_no_news"] & (volume["day_return"] > 0)].sort_values(["symbol", "session_index"])
    kept = []
    last: dict[str, int] = {}
    for symbol, index in zip(chosen["symbol"], chosen["session_index"]):
        if symbol in last and index - last[symbol] < spec.DEDUPE_SESSIONS:
            continue
        last[symbol] = int(index)
        kept.append({"symbol": symbol, "knowledge_index": int(index)})
    return pd.DataFrame.from_records(kept)


def _nepse_daily(panel: es.Panel, index: pd.DataFrame) -> np.ndarray:
    level = index.set_index("date")["close"].reindex(pd.Index(panel.sessions)).ffill()
    return level.pct_change().fillna(0.0).to_numpy()


def run_trades(
    panel: es.Panel,
    events: pd.DataFrame,
    hold: int,
    seasoned: bool,
    first: dict[str, int],
    universe: np.ndarray,
    sectors: dict,
    nepse: np.ndarray,
) -> tuple[pd.DataFrame, dict[str, int]]:
    counts = {"events": int(len(events)), "ineligible": 0}
    keep = []
    for symbol, knowledge in zip(events["symbol"], events["knowledge_index"]):
        if knowledge < 0 or knowledge >= len(panel.sessions) or not eligible(panel, symbol, int(knowledge), seasoned, first):
            counts["ineligible"] += 1
            continue
        keep.append({"symbol": symbol, "knowledge_date": panel.sessions[int(knowledge)]})
    frame = pd.DataFrame.from_records(keep, columns=["symbol", "knowledge_date"])
    trades, outcomes = es.simulate_trades(panel, frame, hold, last_index=len(panel.sessions) - 1, universe=universe, sectors=sectors)
    counts.update(outcomes)
    if not trades.empty:
        trades["nepse_return"] = [
            float(np.prod(1 + nepse[(e - 1 if rule == es.ENTRY_OPEN else e) + 1 : x + 1]) - 1)
            for e, x, rule in zip(trades["entry_index"], trades["exit_index"], trades["entry_rule"])
        ]
    return trades, counts


def _fold(entry_index: pd.Series, sessions: int) -> pd.Series:
    return entry_index * spec.FOLDS // sessions + 1


def _guard(trades: pd.DataFrame, config) -> None:
    rows = [
        Row(
            symbol=t.symbol,
            signal_date=t.knowledge_date,
            label=Label(t.entry_date, t.entry_price, t.exit_date, t.entry_price, t.gross_return, t.exit_status),
        )
        for t in trades.itertuples(index=False)
    ]
    partition = partition_rows(rows, config)
    if partition.holdout.count or partition.boundary_dropped:
        raise SystemExit("a trade reached past the development window")
    assert_development_only(list(partition.development), config, "walk_forward")


def evaluate_events(trades: pd.DataFrame, direction: str, sessions: int, alpha: float, ledger_alpha: float) -> dict[str, Any]:
    if trades.empty:
        return {"trades": 0, "gates": {}, "passes": False}
    trades = trades.copy()
    trades["fold"] = _fold(trades["entry_index"], sessions)
    trades["abnormal"] = trades["gross_return"] - trades["universe_return"]
    trades["abnormal_sector"] = trades["gross_return"] - trades["sector_return"]
    for cost in spec.COST_LEVELS:
        trades[f"excess_{cost}"] = trades["abnormal"] - cost
    trades["sector_excess_1"] = trades["abnormal_sector"] - spec.PRIMARY_COST
    trades["vs_nepse_1"] = trades["gross_return"] - spec.PRIMARY_COST - trades["nepse_return"]
    real_open = trades[trades["entry_date"] >= es.FIRST_REAL_OPEN_SESSION]
    long = direction == "long"
    value = f"excess_{spec.PRIMARY_COST}" if long else "abnormal"
    primary = es.clustered_intervals(trades, value, alpha)
    folds = []
    for fold in range(1, spec.FOLDS + 1):
        group = trades[trades["fold"] == fold]
        mean = float(group[value].mean()) if len(group) else float("nan")
        enough = group["entry_date"].nunique() >= spec.MIN_FOLD_EVENT_DATES
        consistent = enough and (mean > 0 if long else mean < 0)
        folds.append(
            {"fold": fold, "trades": int(len(group)), "entry_dates": int(group["entry_date"].nunique()),
             "mean_pct": round(mean * 100, 3) if len(group) else None, "consistent": bool(consistent)}
        )
    upper = None
    if primary["by_date"] and primary.get("by_20_session_block"):
        upper = max(primary["by_date"]["high_pct"], primary["by_20_session_block"]["high_pct"])
    mean_value = float(trades[value].mean())
    if long:
        gates = {
            "G1": primary["conservative_low_pct"] is not None and primary["conservative_low_pct"] > 0,
            "G2": bool(trades[f"excess_{spec.STRESS_COST}"].mean() > 0),
            "G3": sum(f["consistent"] for f in folds) >= spec.MIN_FOLDS_CONSISTENT,
            "G4": bool(trades["sector_excess_1"].mean() > 0),
            "G5": bool(len(real_open) > 0 and real_open[value].mean() > 0),
            "G6": bool(trades["vs_nepse_1"].mean() > 0),
        }
    else:
        gates = {
            "G1": upper is not None and upper < 0,
            "G2": bool(mean_value <= spec.AVOID_MIN_ABNORMAL),
            "G3": sum(f["consistent"] for f in folds) >= spec.MIN_FOLDS_CONSISTENT,
            "G4": bool(trades["abnormal_sector"].mean() < 0),
            "G5": bool(len(real_open) > 0 and real_open[value].mean() < 0),
        }
    pct = lambda s: round(float(s.mean()) * 100, 3) if len(s) else None
    return {
        "trades": int(len(trades)),
        "entry_dates": int(trades["entry_date"].nunique()),
        "symbols": int(trades["symbol"].nunique()),
        "first_entry": str(trades["entry_date"].min()),
        "last_exit": str(trades["exit_date"].max()),
        "mean_gross_pct": pct(trades["gross_return"]),
        "mean_universe_pct": pct(trades["universe_return"]),
        "mean_abnormal_gross_pct": pct(trades["abnormal"]),
        "mean_abnormal_vs_sector_gross_pct": pct(trades["abnormal_sector"]),
        "mean_nepse_pct": pct(trades["nepse_return"]),
        "abnormal_gross": es.clustered_intervals(trades, "abnormal", spec.BASE_ALPHA),
        "excess_by_cost": {f"{c * 100:.1f}%": es.clustered_intervals(trades, f"excess_{c}", spec.BASE_ALPHA) for c in spec.COST_LEVELS},
        "primary_at_family_alpha": primary,
        "primary_at_ledger_alpha": es.clustered_intervals(trades, value, ledger_alpha),
        "sector_excess_1pct_pct": pct(trades["sector_excess_1"]),
        "vs_nepse_1pct_pct": pct(trades["vs_nepse_1"]),
        "real_open_trades": int(len(real_open)),
        "real_open_primary_pct": pct(real_open[value]) if len(real_open) else None,
        "folds": folds,
        "exit_status": trades["exit_status"].value_counts().to_dict(),
        "entry_rule": trades["entry_rule"].value_counts().to_dict(),
        "mean_entry_delay": round(float(trades["entry_delay"].mean()), 2),
        "gates": gates,
        "passes": all(gates.values()),
    }


def _sharpe(returns: np.ndarray) -> float:
    sd = returns.std(ddof=1)
    return float(returns.mean() / sd * np.sqrt(spec.ANNUALIZATION)) if sd > 0 else float("nan")


def _max_drawdown(returns: np.ndarray) -> float:
    wealth = np.cumprod(1 + returns)
    peak = np.maximum.accumulate(wealth)
    return float((wealth / peak - 1).min())


def market_rule_returns(state: pd.DataFrame, instrument: np.ndarray, switch_cost: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    signal = ((state["trend_up"] == 1.0) & (state["breadth_above_sma50"] > spec.MARKET_BREADTH_MIN)).to_numpy()
    defined = state["nepse_sma_200"].notna().to_numpy()
    start = int(np.flatnonzero(defined)[0])
    rf = np.nan_to_num(state["tbill_rate_known"].to_numpy() / 100.0 / spec.ANNUALIZATION, nan=0.0)
    position = np.zeros(len(state), dtype=bool)
    position[1:] = signal[:-1]
    position[: start + 1] = True
    strategy = np.where(position, instrument, rf)
    switches = np.zeros(len(state), dtype=bool)
    switches[1:] = position[1:] != position[:-1]
    strategy = strategy - switches * (switch_cost / 2)
    return strategy[start + 1 :], instrument[start + 1 :], position[start + 1 :]


def _bootstrap_sharpe_difference(strategy: np.ndarray, benchmark: np.ndarray, alpha: float) -> dict[str, float]:
    rng = np.random.default_rng(spec.MARKET_BOOTSTRAP_SEED)
    n = len(strategy)
    block = spec.MARKET_BOOTSTRAP_BLOCK
    starts_count = n - block + 1
    draws = []
    for _ in range(spec.MARKET_BOOTSTRAP_REPS):
        starts = rng.integers(0, starts_count, size=int(np.ceil(n / block)))
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel()[:n]
        draws.append(_sharpe(strategy[idx]) - _sharpe(benchmark[idx]))
    draws = np.array(draws)
    return {
        "low": float(np.quantile(draws, alpha / 2)),
        "high": float(np.quantile(draws, 1 - alpha / 2)),
        "share_positive": float((draws > 0).mean()),
    }


def evaluate_market_rule(state: pd.DataFrame, nepse: np.ndarray, universe: np.ndarray, alpha: float) -> dict[str, Any]:
    result: dict[str, Any] = {}
    strategy, benchmark, position = market_rule_returns(state, nepse, spec.MARKET_SWITCH_COST_ROUND_TRIP)
    stress, _, _ = market_rule_returns(state, nepse, spec.MARKET_STRESS_SWITCH_COST)
    dates = state["date"].to_numpy()[-len(strategy) :]
    folds = []
    edges = np.linspace(0, len(strategy), spec.FOLDS + 1).astype(int)
    for fold in range(spec.FOLDS):
        lo, hi = edges[fold], edges[fold + 1]
        s, b = _sharpe(strategy[lo:hi]), _sharpe(benchmark[lo:hi])
        folds.append(
            {"fold": fold + 1, "start": str(dates[lo]), "end": str(dates[hi - 1]), "sharpe_rule": round(s, 3),
             "sharpe_buy_hold": round(b, 3), "max_dd_rule_pct": round(_max_drawdown(strategy[lo:hi]) * 100, 2),
             "max_dd_buy_hold_pct": round(_max_drawdown(benchmark[lo:hi]) * 100, 2), "rule_better": bool(s > b)}
        )
    boot = _bootstrap_sharpe_difference(strategy, benchmark, alpha)
    sharpe_rule = _sharpe(strategy)
    sharpe_bh = _sharpe(benchmark)
    dd_rule = _max_drawdown(strategy)
    dd_bh = _max_drawdown(benchmark)
    years = len(strategy) / spec.ANNUALIZATION
    gates = {
        "G1": sharpe_rule > sharpe_bh,
        "G2": sum(f["rule_better"] for f in folds) >= spec.MIN_FOLDS_CONSISTENT,
        "G3": abs(dd_rule) <= spec.MARKET_DRAWDOWN_RATIO_MAX * abs(dd_bh),
        "G4": _sharpe(stress) > sharpe_bh,
        "G5": boot["low"] > 0,
    }
    universe_rule, universe_bh, _ = market_rule_returns(state, universe, spec.MARKET_SWITCH_COST_ROUND_TRIP)
    result.update(
        {
            "start": str(dates[0]),
            "end": str(dates[-1]),
            "sessions": int(len(strategy)),
            "share_sessions_in": round(float(position.mean()), 3),
            "switches": int(np.sum(position[1:] != position[:-1])),
            "sharpe_rule_1pct": round(sharpe_rule, 3),
            "sharpe_rule_1_5pct": round(_sharpe(stress), 3),
            "sharpe_buy_hold": round(sharpe_bh, 3),
            "cagr_rule_pct": round(((np.prod(1 + strategy)) ** (1 / years) - 1) * 100, 2),
            "cagr_buy_hold_pct": round(((np.prod(1 + benchmark)) ** (1 / years) - 1) * 100, 2),
            "max_drawdown_rule_pct": round(dd_rule * 100, 2),
            "max_drawdown_buy_hold_pct": round(dd_bh * 100, 2),
            "bootstrap_sharpe_difference": {k: round(v, 3) for k, v in boot.items()},
            "folds": folds,
            "diagnostic_equal_weight_universe_not_gate": {
                "sharpe_rule": round(_sharpe(universe_rule), 3),
                "sharpe_buy_hold": round(_sharpe(universe_bh), 3),
                "max_drawdown_rule_pct": round(_max_drawdown(universe_rule) * 100, 2),
                "max_drawdown_buy_hold_pct": round(_max_drawdown(universe_bh) * 100, 2),
            },
            "gates": gates,
            "passes": all(gates.values()),
        }
    )
    return result


def _verify_registration() -> int:
    from sqlalchemy import text

    from src.database.connection import engine

    with engine.connect() as connection:
        registered = {
            row[0]
            for row in connection.execute(
                text("SELECT variant_fingerprint FROM backtest_variant_trials WHERE model_family = :f"),
                {"f": spec.MODEL_FAMILY},
            )
        }
        total = int(connection.execute(text("SELECT count(*) FROM backtest_variant_trials")).scalar_one())
    expected = {variant_fingerprint(spec.variant_parameters(h)) for h in spec.HYPOTHESES}
    if registered != expected:
        raise SystemExit("registered event variants do not match the pre-registration")
    return total


def run(output: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    started = time.perf_counter()
    ledger_variants = _verify_registration()
    config = load_holdout_config(CONFIG_PATH)
    inputs = load_inputs(spec.DEVELOPMENT_END)
    panel = load_panel(inputs)
    if panel.sessions[-1] > spec.DEVELOPMENT_END:
        raise SystemExit("panel extends past the development window")
    counter = es.TestCounter(spec.MODEL_FAMILY, spec.BASE_ALPHA, planned=len(spec.HYPOTHESES))
    alpha = counter.alpha
    ledger_alpha = spec.BASE_ALPHA / ledger_variants
    universe = es.benchmark_returns(panel)
    sectors = es.sector_benchmarks(panel)
    nepse = _nepse_daily(panel, inputs["index"])
    first = _first_index(panel)
    actions = inputs["actions"][inputs["actions"]["action_type"].isin(["BONUS", "DIVIDEND", "RIGHT"])]
    listings = et.new_listing_events(panel, et._merger_symbols(inputs))
    up_close, _, _, _ = et.circuit_flags(panel)
    streaks = et.circuit_streak_events(panel, listings)
    ends = et.streak_end_events(streaks, panel)
    volume = et.volume_anomaly_events(panel, actions, listings)
    state = et.market_state(panel, inputs["index"], inputs["rates"])

    event_sets = {
        "E1": book_close_set(panel, actions, spec.HYPOTHESES["E1"]["knowledge_offset_from_ex"]),
        "E2": book_close_set(panel, actions, spec.HYPOTHESES["E2"]["knowledge_offset_from_ex"]),
        "E3": new_listing_set(panel, listings, up_close),
        "E4": streak_end_set(ends),
        "E5": volume_set(volume),
    }
    event_sets["E7"] = event_sets["E5"]
    event_sets["E8"] = event_sets["E5"]

    results: dict[str, Any] = {}
    for hypothesis_id, hypothesis in spec.HYPOTHESES.items():
        counter.record(hypothesis_id)
        if hypothesis["direction"] == "timing":
            results[hypothesis_id] = {"name": hypothesis["name"], **evaluate_market_rule(state, nepse, universe, alpha)}
            continue
        trades, counts = run_trades(
            panel, event_sets[hypothesis_id], hypothesis["hold_sessions"], hypothesis["seasoned"], first, universe, sectors, nepse
        )
        if not trades.empty:
            _guard(trades, config)
        results[hypothesis_id] = {
            "name": hypothesis["name"],
            "direction": hypothesis["direction"],
            "hold_sessions": hypothesis["hold_sessions"],
            "event_counts": counts,
            **evaluate_events(trades, hypothesis["direction"], len(panel.sessions), alpha, ledger_alpha),
        }
        logger.info("%s done", hypothesis_id)

    report = {
        "protocol": spec.PROTOCOL_VERSION,
        "generated_on": date.today().isoformat(),
        "config_version": config.version,
        "config_sha256": config.sha256,
        "last_session_loaded": panel.sessions[-1].isoformat(),
        "sessions": len(panel.sessions),
        "fold_ranges": [
            [f + 1, panel.sessions[f * len(panel.sessions) // spec.FOLDS].isoformat(),
             panel.sessions[(f + 1) * len(panel.sessions) // spec.FOLDS - 1].isoformat()]
            for f in range(spec.FOLDS)
        ],
        "tests_run": counter.tests,
        "family_alpha": alpha,
        "ledger_variants": ledger_variants,
        "ledger_alpha": ledger_alpha,
        "hypotheses": results,
        "runtime_seconds": round(time.perf_counter() - started, 1),
    }
    output.write_text(json.dumps(report, indent=2, default=str))
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run(args.output)
    print(json.dumps({h: {"gates": r["gates"], "passes": r["passes"]} for h, r in report["hypotheses"].items()}, indent=2))


if __name__ == "__main__":
    main()
