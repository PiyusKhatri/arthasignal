from __future__ import annotations

import argparse
import json
import logging
import math
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import mean
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from src.backtest import broker_flow_spec as spec
from src.backtest.baselines import simple_momentum_selection
from src.backtest.config import HoldoutConfig, load_holdout_config
from src.backtest.entry import benchmark_return, next_open_label
from src.backtest.holdout import assert_development_only, partition_rows
from src.backtest.ledger import variant_fingerprint
from src.backtest.signal_rerun import discontinuity_dates, event_inside_window
from src.backtest.splits import walk_forward_folds
from src.backtest.stats import adjusted_alpha, clustered_mean_interval
from src.backtest.types import Bar, Label, Row

logger = logging.getLogger(__name__)

DEFAULT_FEATURES = Path("~/Desktop/arthasignal-ai/derived/broker_flow/features.parquet").expanduser()
DEFAULT_OUTPUT = Path("docs/broker_flow_results.json")
LABEL_LOOKAHEAD_SESSIONS = 250
ROC_PERIOD = 12
ENTRY_OPEN = "next_open"
ENTRY_CLOSE = "next_close"


@dataclass(frozen=True)
class MarketData:
    sessions: list[date]
    bars: dict[str, list[dict[str, Any]]]
    nepse: dict[date, Bar]
    actions: dict[str, list[date]]


def load_market_data(end: date = spec.DEVELOPMENT_END) -> MarketData:
    from sqlalchemy import text

    from src.database.connection import engine

    with engine.connect() as connection:
        sessions = [
            row[0]
            for row in connection.execute(
                text("SELECT DISTINCT date FROM daily_prices WHERE date >= :s AND date <= :e ORDER BY 1"),
                {"s": spec.DEVELOPMENT_START, "e": end},
            )
        ]
        price_rows = connection.execute(
            text(
                "SELECT p.symbol, p.date, p.open, p.close FROM daily_prices p "
                "JOIN companies c ON c.symbol = p.symbol AND c.instrument_type = 'Equity' "
                "WHERE p.date <= :e ORDER BY p.symbol, p.date"
            ),
            {"e": end},
        ).all()
        index_rows = connection.execute(
            text("SELECT date, open, close FROM market_index WHERE index_name = 'NEPSE Index' AND date <= :e"),
            {"e": end},
        ).all()
        action_rows = connection.execute(
            text(
                "SELECT symbol, action_date FROM corporate_actions "
                "WHERE action_type::text IN ('BONUS', 'RIGHT') ORDER BY symbol, action_date"
            )
        ).all()
    bars: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for symbol, day, open_price, close in price_rows:
        bars[symbol].append({"date": day, "open": float(open_price), "close": float(close)})
    nepse = {day: Bar(open=float(o), close=float(c)) for day, o, c in index_rows}
    actions: dict[str, list[date]] = defaultdict(list)
    for symbol, day in action_rows:
        actions[symbol].append(day)
    return MarketData(sessions=sessions, bars=dict(bars), nepse=nepse, actions=dict(actions))


def label_for(
    signal_date: date,
    session_index: Mapping[date, int],
    sessions: Sequence[date],
    open_bars: Mapping[date, Bar],
    close_bars: Mapping[date, Bar],
) -> tuple[Label | None, str | None]:
    position = session_index.get(signal_date)
    if position is None or position + 1 >= len(sessions):
        return None, None
    window = sessions[position : position + spec.HORIZON_SESSIONS + 1 + spec.GRACE_SESSIONS + LABEL_LOOKAHEAD_SESSIONS]
    if sessions[position + 1] >= spec.FIRST_REAL_OPEN_SESSION:
        return (
            next_open_label(window, open_bars, signal_date, spec.HORIZON_SESSIONS, spec.GRACE_SESSIONS),
            ENTRY_OPEN,
        )
    return (
        next_open_label(window, close_bars, signal_date, spec.HORIZON_SESSIONS + 1, spec.GRACE_SESSIONS),
        ENTRY_CLOSE,
    )


def nepse_return(nepse: Mapping[date, Bar], label: Label, entry_rule: str) -> float | None:
    if entry_rule == ENTRY_OPEN:
        return benchmark_return(nepse, label)
    entry = nepse.get(label.entry_date)
    exit_bar = nepse.get(label.exit_date)
    if entry is None or exit_bar is None or not entry.close or not exit_bar.close:
        return None
    return exit_bar.close / entry.close - 1.0


def build_labeled_rows(
    market: MarketData,
    eligible: pd.DataFrame,
) -> tuple[list[Row], dict[tuple[str, date], str], dict[str, int]]:
    session_index = {day: i for i, day in enumerate(market.sessions)}
    wanted: dict[str, set[date]] = defaultdict(set)
    for symbol, day in zip(eligible["symbol"], eligible["date"]):
        wanted[symbol].add(day)
    rows: list[Row] = []
    entry_rules: dict[tuple[str, date], str] = {}
    exclusions = {"no_label": 0, "corporate_action": 0, "price_discontinuity": 0, "no_price_bars": 0}
    for symbol, days in wanted.items():
        bars = market.bars.get(symbol)
        if not bars:
            exclusions["no_price_bars"] += len(days)
            continue
        open_bars = {bar["date"]: Bar(open=bar["open"], close=bar["close"]) for bar in bars}
        close_bars = {bar["date"]: Bar(open=bar["close"], close=bar["close"]) for bar in bars}
        jumps = discontinuity_dates(bars)
        closes = [bar["close"] for bar in bars]
        position = {bar["date"]: i for i, bar in enumerate(bars)}
        for day in sorted(days):
            label, rule = label_for(day, session_index, market.sessions, open_bars, close_bars)
            if label is None:
                exclusions["no_label"] += 1
                continue
            if event_inside_window(market.actions.get(symbol, []), day, label.exit_date):
                exclusions["corporate_action"] += 1
                continue
            if event_inside_window(jumps, day, label.exit_date):
                exclusions["price_discontinuity"] += 1
                continue
            i = position.get(day)
            momentum = None
            if i is not None and i >= ROC_PERIOD and closes[i - ROC_PERIOD] > 0:
                momentum = closes[i] / closes[i - ROC_PERIOD] - 1.0
            rows.append(
                Row(
                    symbol=symbol,
                    signal_date=day,
                    label=label,
                    benchmark_return=nepse_return(market.nepse, label, rule),
                    momentum_score=momentum,
                )
            )
            entry_rules[(symbol, day)] = rule
    return rows, entry_rules, exclusions


def select_portfolio(frame: pd.DataFrame, feature: str, direction: str) -> pd.DataFrame:
    ordered = frame.assign(_key=frame[feature] * (-1.0 if direction == "high" else 1.0)).sort_values(
        ["_key", "symbol"]
    )
    breadth = math.ceil(spec.QUINTILE * len(ordered))
    return ordered.head(breadth)


def daily_table(
    frame: pd.DataFrame,
    feature: str,
    direction: str,
    rows_by_key: Mapping[tuple[str, date], Row],
) -> pd.DataFrame:
    records = []
    opposite = "low" if direction == "high" else "high"
    for day, group in frame.groupby("date", sort=True):
        group = group[group[feature].notna()]
        if len(group) < spec.MIN_ELIGIBLE_PER_DATE:
            continue
        chosen = select_portfolio(group, feature, direction)
        worst = select_portfolio(group, feature, opposite)
        breadth = len(chosen)
        date_rows = [rows_by_key[(symbol, day)] for symbol in group["symbol"]]
        momentum = simple_momentum_selection(date_rows, breadth)
        universe = float(group["gross"].mean())
        signed = group[feature] * (1.0 if direction == "high" else -1.0)
        records.append(
            {
                "date": day,
                "n": len(group),
                "k": breadth,
                "portfolio": float(chosen["gross"].mean()),
                "universe": universe,
                "nepse": float(chosen["nepse"].mean()) if chosen["nepse"].notna().any() else np.nan,
                "momentum": float(np.mean([row.label.gross_return for row in momentum])) if len(momentum) == breadth else np.nan,
                "opposite": float(worst["gross"].mean()),
                "ic": float(signed.rank().corr((group["gross"] - universe).rank())),
                "entry_rule": group["entry_rule"].iloc[0],
                "symbols": tuple(chosen["symbol"]),
            }
        )
    return pd.DataFrame.from_records(records)


def _interval(values_by_cluster: Mapping[Any, Sequence[float]], alpha: float) -> dict[str, Any] | None:
    interval = clustered_mean_interval(values_by_cluster, alpha)
    if interval is None:
        return None
    return {
        "mean_pct": round(interval.mean * 100, 3),
        "low_pct": round(interval.low * 100, 3),
        "high_pct": round(interval.high * 100, 3),
        "clusters": interval.clusters,
        "alpha": round(alpha, 6),
    }


def intervals(daily: pd.DataFrame, column: str, alpha: float) -> dict[str, Any]:
    clean = daily[daily[column].notna()]
    by_date = {day: [value] for day, value in zip(clean["date"], clean[column])}
    by_block: dict[int, list[float]] = defaultdict(list)
    for block, value in zip(clean["block"], clean[column]):
        by_block[int(block)].append(float(value))
    date_interval = _interval(by_date, alpha)
    block_interval = _interval(by_block, alpha)
    lows = [i["low_pct"] for i in (date_interval, block_interval) if i is not None]
    return {
        "by_date": date_interval,
        "by_20_session_block": block_interval,
        "conservative_low_pct": min(lows) if lows else None,
    }


def evaluate_hypothesis(daily: pd.DataFrame, family_alpha: float, ledger_alpha: float) -> dict[str, Any]:
    if daily.empty:
        return {"dates": 0, "gates": {f"G{i}": False for i in range(1, 7)}, "passes": False}
    pooled = daily[daily["fold"].notna()].copy()
    for cost in spec.COST_LEVELS:
        pooled[f"excess_{cost}"] = pooled["portfolio"] - cost - pooled["universe"]
        pooled[f"net_{cost}"] = pooled["portfolio"] - cost
    primary = f"excess_{spec.PRIMARY_COST}"
    stress = f"excess_{spec.STRESS_COST}"
    pooled["vs_nepse"] = pooled["portfolio"] - spec.PRIMARY_COST - pooled["nepse"]
    pooled["vs_momentum"] = pooled["portfolio"] - pooled["momentum"]
    pooled["momentum_excess"] = pooled["momentum"] - spec.PRIMARY_COST - pooled["universe"]
    pooled["spread"] = pooled["portfolio"] - pooled["opposite"]

    primary_interval = intervals(pooled, primary, family_alpha)
    folds = []
    for fold, group in pooled.groupby("fold"):
        value = float(group[primary].mean())
        folds.append(
            {
                "fold": int(fold),
                "start": group["date"].min().isoformat(),
                "end": group["date"].max().isoformat(),
                "dates": int(len(group)),
                "mean_excess_1pct_pct": round(value * 100, 3),
                "positive": bool(len(group) >= spec.MIN_FOLD_SIGNAL_DATES and value > 0),
            }
        )
    real_open = pooled[pooled["entry_rule"] == ENTRY_OPEN]
    gates = {
        "G1": primary_interval["conservative_low_pct"] is not None and primary_interval["conservative_low_pct"] > 0,
        "G2": bool(pooled[stress].mean() > 0),
        "G3": sum(1 for f in folds if f["positive"]) >= spec.MIN_FOLDS_POSITIVE,
        "G4": bool(pooled["vs_nepse"].dropna().mean() > 0),
        "G5": bool(pooled["vs_momentum"].dropna().mean() > 0),
        "G6": bool(len(real_open) > 0 and real_open[primary].mean() > 0),
    }
    turnover = []
    previous: set[str] | None = None
    for symbols in pooled["symbols"]:
        current = set(symbols)
        if previous:
            turnover.append(1 - len(current & previous) / max(len(current), 1))
        previous = current
    pct = lambda series: round(float(series.dropna().mean()) * 100, 3)
    return {
        "dates": int(len(daily)),
        "pooled_dates": int(len(pooled)),
        "mean_eligible_per_date": round(float(pooled["n"].mean()), 1),
        "mean_portfolio_size": round(float(pooled["k"].mean()), 1),
        "mean_portfolio_gross_pct": pct(pooled["portfolio"]),
        "mean_universe_gross_pct": pct(pooled["universe"]),
        "excess": {
            f"{cost * 100:.1f}%": intervals(pooled, f"excess_{cost}", spec.BASE_ALPHA) for cost in spec.COST_LEVELS
        },
        "excess_1pct_family_alpha": primary_interval,
        "excess_1pct_ledger_alpha": intervals(pooled, primary, ledger_alpha),
        "vs_nepse_1pct_pct": pct(pooled["vs_nepse"]),
        "nepse_mean_pct": pct(pooled["nepse"]),
        "vs_momentum_pct": pct(pooled["vs_momentum"]),
        "momentum_excess_1pct_pct": pct(pooled["momentum_excess"]),
        "real_open_dates": int(len(real_open)),
        "real_open_excess_1pct_pct": pct(real_open[primary]) if len(real_open) else None,
        "close_entry_dates": int((pooled["entry_rule"] == ENTRY_CLOSE).sum()),
        "close_entry_excess_1pct_pct": pct(pooled.loc[pooled["entry_rule"] == ENTRY_CLOSE, primary]),
        "folds": folds,
        "diagnostics_not_gates": {
            "mean_rank_ic": round(float(pooled["ic"].mean()), 4),
            "share_of_dates_ic_positive_pct": round(float((pooled["ic"] > 0).mean() * 100), 1),
            "top_minus_bottom_quintile_pct": pct(pooled["spread"]),
            "daily_turnover_pct": round(float(np.mean(turnover)) * 100, 1) if turnover else None,
        },
        "gates": gates,
        "passes": all(gates.values()),
    }


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
    missing = [h for h in spec.HYPOTHESES if variant_fingerprint(spec.variant_parameters(h)) not in registered]
    if missing or len(registered) != len(spec.HYPOTHESES):
        raise SystemExit(f"registered variants do not match the pre-registration: missing {missing}, found {len(registered)}")
    return total


def run(features_path: Path = DEFAULT_FEATURES, output: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    started = time.perf_counter()
    ledger_variants = _verify_registration()
    config: HoldoutConfig = load_holdout_config(spec.CONFIG_PATH)
    features = pd.read_parquet(features_path)
    features["date"] = pd.to_datetime(features["date"]).dt.date
    if features["date"].max() > spec.DEVELOPMENT_END:
        raise SystemExit("feature store contains dates after the development window")
    eligible = features[features["eligible"]].copy()

    market = load_market_data()
    if market.sessions[-1] > spec.DEVELOPMENT_END:
        raise SystemExit("price sessions after the development window were loaded")
    rows, entry_rules, exclusions = build_labeled_rows(market, eligible)
    logger.info("labeled %d rows in %.0fs", len(rows), time.perf_counter() - started)

    partition = partition_rows(rows, config)
    development = list(partition.development)
    assert_development_only(development, config, "walk_forward")
    if partition.holdout.count or partition.boundary_dropped:
        raise SystemExit("rows reached past the development window")

    rows_by_key = {row.key: row for row in development}
    labels = pd.DataFrame(
        {
            "symbol": [row.symbol for row in development],
            "date": [row.signal_date for row in development],
            "gross": [row.label.gross_return for row in development],
            "nepse": [row.benchmark_return for row in development],
            "exit_date": [row.label.exit_date for row in development],
            "exit_status": [row.label.exit_status for row in development],
            "entry_rule": [entry_rules[row.key] for row in development],
        }
    )
    frame = eligible.merge(labels, on=["symbol", "date"], how="inner")

    session_index = {day: i for i, day in enumerate(market.sessions)}
    folds = walk_forward_folds(sorted(frame["date"].unique()), config)
    fold_of = {day: fold.index for fold in folds for day in fold.test_sessions}

    family_alpha = adjusted_alpha(spec.BASE_ALPHA, len(spec.HYPOTHESES))
    ledger_alpha = adjusted_alpha(spec.BASE_ALPHA, ledger_variants)
    results: dict[str, Any] = {}
    for hypothesis_id, hypothesis in spec.HYPOTHESES.items():
        feature = hypothesis_id.lower()
        daily = daily_table(frame, feature, hypothesis["direction"], rows_by_key)
        if not daily.empty:
            daily["fold"] = daily["date"].map(fold_of)
            daily["block"] = daily["date"].map(lambda d: session_index[d] // spec.CLUSTER_BLOCK_SESSIONS)
        results[hypothesis_id] = {
            "feature": hypothesis["name"],
            "direction": hypothesis["direction"],
            **evaluate_hypothesis(daily, family_alpha, ledger_alpha),
        }
        logger.info("%s evaluated", hypothesis_id)

    universe_daily = frame.groupby("date")["gross"].mean()
    report = {
        "protocol": spec.PROTOCOL_VERSION,
        "generated_on": date.today().isoformat(),
        "config_version": config.version,
        "config_sha256": config.sha256,
        "development_end": spec.DEVELOPMENT_END.isoformat(),
        "last_price_session_loaded": market.sessions[-1].isoformat(),
        "eligible_feature_rows": int(len(eligible)),
        "labeled_rows": int(len(development)),
        "exclusions": exclusions,
        "exit_status": frame["exit_status"].value_counts().to_dict(),
        "entry_rule_rows": frame["entry_rule"].value_counts().to_dict(),
        "signal_dates": int(frame["date"].nunique()),
        "first_signal_date": frame["date"].min().isoformat(),
        "last_signal_date": frame["date"].max().isoformat(),
        "last_exit_date": frame["exit_date"].max().isoformat(),
        "holdout_rows": partition.holdout.count,
        "boundary_rows_dropped": partition.boundary_dropped,
        "family_alpha": family_alpha,
        "ledger_variants": ledger_variants,
        "ledger_alpha": ledger_alpha,
        "walk_forward_folds": [
            {"fold": f.index, "test_start": f.test_sessions[0].isoformat(), "test_end": f.test_sessions[-1].isoformat(),
             "sessions": len(f.test_sessions), "gap_sessions": f.gap_sessions}
            for f in folds
        ],
        "universe_mean_gross_pct_all_dates": round(float(universe_daily.mean()) * 100, 3),
        "hypotheses": results,
        "runtime_seconds": round(time.perf_counter() - started, 1),
    }
    output.write_text(json.dumps(report, indent=2, default=str))
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Evaluate the pre-registered broker-flow hypotheses on the development window")
    parser.add_argument("--features", type=Path, default=DEFAULT_FEATURES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run(args.features, args.output)
    print(json.dumps({h: {"gates": r["gates"], "passes": r["passes"]} for h, r in report["hypotheses"].items()}, indent=2))


if __name__ == "__main__":
    main()
