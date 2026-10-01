from __future__ import annotations

import argparse
import bisect
import json
import logging
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import mean
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from sqlalchemy import func, select

from src.backtest.config import HoldoutConfig, load_holdout_config
from src.backtest.entry import benchmark_return, next_open_label
from src.backtest.holdout import assert_development_only, partition_rows
from src.backtest.ledger import DatabaseLedger
from src.backtest.splits import fold_rows, walk_forward_folds
from src.backtest.stats import adjusted_alpha, clustered_mean_interval
from src.backtest.types import Bar, Label, Row
from src.backtest.universe import load_survivorship_coverage
from src.database.connection import get_session
from src.database.models import Company, CorporateAction, DailyPrice, MarketIndex, SignalTimeframe
from src.pipeline.compute_signals import _compute_series
from src.pipeline.data_quality import _circuit_breaker_flag_threshold
from src.pipeline.run_signal_backtests import build_signal_conditions

logger = logging.getLogger(__name__)

FIRST_REAL_OPEN_SESSION = date(2018, 2, 18)
OLD_STUDY_START = date(2021, 7, 25)
PRICE_ADJUSTING_ACTIONS = {"BONUS", "RIGHT"}
LABEL_LOOKAHEAD_SESSIONS = 250
MODEL_FAMILY = "rule_signal_rerun_2026_10"
NEW_LISTING_SESSIONS = 60
FIRST_LISTING_VISIBLE_AFTER = date(2014, 7, 1)
DEFAULT_OUTPUT = Path("docs/backtest_rerun_results.json")


def _load_equity_bars(start: date | None = None) -> dict[str, list[dict[str, Any]]]:
    with get_session() as session:
        query = (
            select(
                DailyPrice.symbol,
                DailyPrice.date,
                DailyPrice.open,
                DailyPrice.high,
                DailyPrice.low,
                DailyPrice.close,
                DailyPrice.volume,
            )
            .join(Company, Company.symbol == DailyPrice.symbol)
            .where(Company.instrument_type == "Equity")
            .order_by(DailyPrice.symbol, DailyPrice.date)
        )
        if start is not None:
            query = query.where(DailyPrice.date >= start)
        rows = session.execute(query).all()
    bars: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        bars[row.symbol].append(
            {"date": row.date, "open": row.open, "high": row.high, "low": row.low, "close": row.close, "volume": row.volume}
        )
    return bars


def _market_sessions() -> list[date]:
    with get_session() as session:
        return list(session.execute(select(DailyPrice.date).distinct().order_by(DailyPrice.date)).scalars())


def _price_adjusting_action_dates() -> dict[str, list[date]]:
    with get_session() as session:
        rows = session.execute(
            select(CorporateAction.symbol, CorporateAction.action_date)
            .where(CorporateAction.action_type.in_(PRICE_ADJUSTING_ACTIONS))
            .order_by(CorporateAction.symbol, CorporateAction.action_date)
        ).all()
    actions: dict[str, list[date]] = defaultdict(list)
    for symbol, action_date in rows:
        actions[symbol].append(action_date)
    return actions


def _nepse_bars() -> dict[date, Bar]:
    with get_session() as session:
        rows = session.execute(
            select(MarketIndex.date, MarketIndex.open, MarketIndex.close).where(MarketIndex.index_name == "NEPSE Index")
        ).all()
    return {row.date: Bar(open=float(row.open), close=float(row.close)) for row in rows}


def event_inside_window(event_dates: Sequence[date], signal_date: date, exit_date: date) -> bool:
    position = bisect.bisect_right(event_dates, signal_date)
    return position < len(event_dates) and event_dates[position] <= exit_date


def discontinuity_dates(bars: Sequence[Mapping[str, Any]]) -> list[date]:
    found = []
    for previous, current in zip(bars, bars[1:]):
        previous_close = float(previous["close"])
        if previous_close <= 0:
            continue
        change = abs(float(current["close"]) / previous_close - 1.0) * 100.0
        if change > _circuit_breaker_flag_threshold(current["date"]):
            found.append(current["date"])
    return found


def label_for(
    signal_date: date,
    session_index: Mapping[date, int],
    sessions: Sequence[date],
    bars_by_date: Mapping[date, Bar],
    config: HoldoutConfig,
) -> Label | None:
    position = session_index.get(signal_date)
    if position is None:
        return None
    window = sessions[position : position + config.horizon_sessions + config.grace_sessions + LABEL_LOOKAHEAD_SESSIONS]
    return next_open_label(window, bars_by_date, signal_date, config.horizon_sessions, config.grace_sessions)


def build_rows(
    config: HoldoutConfig,
    signal_start: date = FIRST_REAL_OPEN_SESSION,
) -> tuple[dict[str, list[Row]], list[Row], dict[str, Any]]:
    conditions = build_signal_conditions()
    sessions = _market_sessions()
    session_index = {day: i for i, day in enumerate(sessions)}
    actions = _price_adjusting_action_dates()
    nepse = _nepse_bars()
    all_bars = _load_equity_bars()

    signal_rows: dict[str, list[Row]] = {name: [] for name in conditions}
    baseline_rows: list[Row] = []
    exclusions = {"no_label": 0, "corporate_action": 0, "price_discontinuity": 0}
    close_entry_returns: dict[str, list[float]] = defaultdict(list)
    started = time.perf_counter()

    for count, (symbol, bars) in enumerate(sorted(all_bars.items()), start=1):
        series = _compute_series(bars, SignalTimeframe.DAILY)
        jumps = discontinuity_dates(bars)
        bar_map = {bar["date"]: Bar(open=float(bar["open"]), close=float(bar["close"])) for bar in bars}
        keys = list(series)
        previous_row = None
        previous_close = None
        for index, bar in enumerate(bars):
            row_view = SimpleNamespace(**{key: series[key][index] for key in keys})
            close = bar["close"]
            fired = [name for name, condition in conditions.items() if condition(row_view, close, previous_row, previous_close)]
            previous_row, previous_close = row_view, close
            signal_date = bar["date"]
            if signal_date < signal_start:
                continue

            label = label_for(signal_date, session_index, sessions, bar_map, config)
            reason = None
            if label is None:
                reason = "no_label"
            elif event_inside_window(actions.get(symbol, []), signal_date, label.exit_date):
                reason = "corporate_action"
            elif event_inside_window(jumps, signal_date, label.exit_date):
                reason = "price_discontinuity"
            if reason is not None:
                exclusions[reason] += 1
                continue

            momentum = series["roc_12"][index]
            row = Row(
                symbol=symbol,
                signal_date=signal_date,
                features={},
                label=label,
                benchmark_return=benchmark_return(nepse, label),
                momentum_score=float(momentum) if momentum is not None else None,
            )
            baseline_rows.append(row)
            close_entry = label.exit_price / float(close) - 1.0
            for name in fired:
                signal_rows[name].append(row)
                close_entry_returns[name].append(close_entry)
            close_entry_returns["__baseline__"].append(close_entry)
        if count % 100 == 0:
            logger.info("Rows built for %d/%d symbols in %.0fs", count, len(all_bars), time.perf_counter() - started)

    meta = {
        "signal_start": signal_start.isoformat(),
        "symbols": len(all_bars),
        "exclusions": exclusions,
        "close_entry_returns": close_entry_returns,
    }
    return signal_rows, baseline_rows, meta


def _interval(values_by_date: Mapping[date, list[float]], alpha: float) -> dict[str, Any] | None:
    interval = clustered_mean_interval(values_by_date, alpha)
    if interval is None:
        return None
    return {
        "mean_pct": round(interval.mean * 100, 3),
        "low_pct": round(interval.low * 100, 3),
        "high_pct": round(interval.high * 100, 3),
        "clusters": interval.clusters,
        "alpha": interval.alpha,
    }


def _listing_context() -> tuple[dict[str, date], dict[date, int], set[tuple[str, date]]]:
    with get_session() as session:
        first = dict(
            session.execute(select(DailyPrice.symbol, func.min(DailyPrice.date)).group_by(DailyPrice.symbol)).all()
        )
        sessions = list(session.execute(select(DailyPrice.date).distinct().order_by(DailyPrice.date)).scalars())
        one_price = set(
            session.execute(
                select(DailyPrice.symbol, DailyPrice.date).where(
                    DailyPrice.date >= FIRST_REAL_OPEN_SESSION,
                    DailyPrice.open == DailyPrice.high,
                    DailyPrice.high == DailyPrice.low,
                )
            ).all()
        )
    return first, {day: i for i, day in enumerate(sessions)}, one_price


def is_new_listing(row: Row, first_dates: Mapping[str, date], session_index: Mapping[date, int]) -> bool:
    first = first_dates.get(row.symbol)
    if first is None or first <= FIRST_LISTING_VISIBLE_AFTER:
        return False
    return session_index[row.signal_date] - session_index[first] < NEW_LISTING_SESSIONS


def _excess_interval(rows: Sequence[Row], baseline_by_date: Mapping[date, float], alpha: float) -> dict[str, Any] | None:
    values: dict[date, list[float]] = defaultdict(list)
    for row in rows:
        values[row.signal_date].append(row.label.gross_return - baseline_by_date[row.signal_date])
    interval = _interval(values, alpha)
    if interval is not None:
        interval["rows"] = len(rows)
    return interval


def listing_diagnostics(
    rows: Sequence[Row],
    baseline_by_date: Mapping[date, float],
    context: tuple[dict[str, date], dict[date, int], set[tuple[str, date]]],
    alpha: float,
) -> dict[str, Any]:
    first_dates, session_index, one_price = context
    young = [row for row in rows if is_new_listing(row, first_dates, session_index)]
    seasoned = [row for row in rows if not is_new_listing(row, first_dates, session_index)]
    tradable = [row for row in seasoned if (row.symbol, row.label.entry_date) not in one_price]
    return {
        "new_listing_share_pct": round(len(young) / len(rows) * 100, 2) if rows else None,
        "one_price_entry_share_pct": round(
            sum(1 for row in rows if (row.symbol, row.label.entry_date) in one_price) / len(rows) * 100, 2
        )
        if rows
        else None,
        "excess_first_60_sessions_only": _excess_interval(young, baseline_by_date, alpha),
        "excess_excluding_first_60_sessions": _excess_interval(seasoned, baseline_by_date, alpha),
        "excess_excluding_first_60_and_one_price_entry": _excess_interval(tradable, baseline_by_date, alpha),
    }


def summarize(
    rows: Sequence[Row],
    baseline_by_date: Mapping[date, float],
    baseline_win_rate: float,
    config: HoldoutConfig,
    alpha_family: float,
) -> dict[str, Any]:
    if not rows:
        return {"rows": 0}
    gross = [row.label.gross_return for row in rows]
    by_date_net: dict[float, dict[date, list[float]]] = {level: defaultdict(list) for level in config.cost_levels_round_trip}
    excess_by_date: dict[date, list[float]] = defaultdict(list)
    nepse_excess_by_date: dict[date, list[float]] = defaultdict(list)
    for row in rows:
        for level in config.cost_levels_round_trip:
            by_date_net[level][row.signal_date].append(row.label.gross_return - level)
        excess_by_date[row.signal_date].append(row.label.gross_return - baseline_by_date[row.signal_date])
        if row.benchmark_return is not None:
            nepse_excess_by_date[row.signal_date].append(row.label.gross_return - row.benchmark_return)
    win_rate = sum(1 for value in gross if value > 0) / len(gross)
    statuses: dict[str, int] = defaultdict(int)
    for row in rows:
        statuses[row.label.exit_status] += 1
    return {
        "rows": len(rows),
        "signal_dates": len(excess_by_date),
        "mean_gross_pct": round(mean(gross) * 100, 3),
        "win_rate_pct": round(win_rate * 100, 2),
        "win_rate_minus_baseline_pts": round((win_rate - baseline_win_rate) * 100, 2),
        "net": {
            f"{level * 100:.1f}%": _interval(by_date_net[level], config.base_alpha)
            for level in config.cost_levels_round_trip
        },
        "net_1pct_family_adjusted": _interval(by_date_net[0.01], alpha_family),
        "excess_vs_equal_weight": _interval(excess_by_date, config.base_alpha),
        "excess_vs_equal_weight_family_adjusted": _interval(excess_by_date, alpha_family),
        "excess_vs_nepse": _interval(nepse_excess_by_date, config.base_alpha),
        "exit_status": dict(statuses),
    }


def run_rerun(output: Path = DEFAULT_OUTPUT, register: bool = True) -> dict[str, Any]:
    config = load_holdout_config()
    signal_rows, baseline_rows, meta = build_rows(config)
    close_entry_returns = meta.pop("close_entry_returns")

    baseline_partition = partition_rows(baseline_rows, config)
    development_baseline = list(baseline_partition.development)
    assert_development_only(development_baseline, config, "walk_forward")
    baseline_gross_by_date: dict[date, list[float]] = defaultdict(list)
    for row in development_baseline:
        baseline_gross_by_date[row.signal_date].append(row.label.gross_return)
    baseline_by_date = {day: mean(values) for day, values in baseline_gross_by_date.items()}
    baseline_win_rate = sum(1 for row in development_baseline if row.label.gross_return > 0) / len(development_baseline)

    signal_names = sorted(signal_rows)
    ledger = DatabaseLedger() if register else None
    if ledger is not None:
        for name in signal_names:
            ledger.register_variant(
                MODEL_FAMILY,
                {"signal": name, "entry": "next_session_open", "horizon": config.horizon_sessions, "prices": "raw"},
                f"Rule signal rerun of {name} with next-open entry on raw prices",
            )
    alpha_family = adjusted_alpha(config.base_alpha, len(signal_names))

    sessions = sorted({row.signal_date for row in development_baseline})
    folds = walk_forward_folds(sessions, config)

    context = _listing_context()
    results: dict[str, Any] = {}
    for name in signal_names:
        partition = partition_rows(signal_rows[name], config)
        development = list(partition.development)
        assert_development_only(development, config, "walk_forward")
        recent = [row for row in development if row.signal_date >= OLD_STUDY_START]
        fold_summaries = []
        for fold in folds:
            _, test = fold_rows(development, fold, config)
            summary = summarize(test, baseline_by_date, baseline_win_rate, config, config.base_alpha)
            fold_summaries.append(
                {
                    "fold": fold.index,
                    "test_start": fold.test_sessions[0].isoformat(),
                    "test_end": fold.test_sessions[-1].isoformat(),
                    "rows": summary["rows"],
                    "net_1pct": (summary.get("net") or {}).get("1.0%"),
                    "excess_vs_equal_weight": summary.get("excess_vs_equal_weight"),
                }
            )
        close_values = close_entry_returns.get(name, [])
        results[name] = {
            "development": summarize(development, baseline_by_date, baseline_win_rate, config, alpha_family),
            "development_since_2021_07_25": summarize(recent, baseline_by_date, baseline_win_rate, config, alpha_family),
            "walk_forward_folds": fold_summaries,
            "post_hoc_listing_diagnostics": listing_diagnostics(development, baseline_by_date, context, config.base_alpha),
            "holdout_rows_sealed": partition.holdout.count,
            "boundary_rows_dropped": partition.boundary_dropped,
            "mean_gross_pct_if_entered_at_signal_close": round(mean(close_values) * 100, 3) if close_values else None,
        }

    with get_session() as session:
        coverage = load_survivorship_coverage(session)

    report = {
        "generated_on": date.today().isoformat(),
        "holdout_config_version": config.version,
        "holdout_config_sha256": config.sha256,
        "holdout_start": config.holdout_start.isoformat(),
        "horizon_sessions": config.horizon_sessions,
        "cost_levels_round_trip": list(config.cost_levels_round_trip),
        "signals_tested": len(signal_names),
        "family_alpha": alpha_family,
        "ledger_variant_count": ledger.variant_count() if ledger is not None else None,
        "rows_meta": meta,
        "baseline": {
            "development_rows": len(development_baseline),
            "development_signal_dates": len(baseline_by_date),
            "mean_gross_pct": round(mean(r.label.gross_return for r in development_baseline) * 100, 3),
            "win_rate_pct": round(baseline_win_rate * 100, 2),
            "mean_gross_pct_if_entered_at_signal_close": round(mean(close_entry_returns["__baseline__"]) * 100, 3),
            "holdout_rows_sealed": baseline_partition.holdout.count,
            "boundary_rows_dropped": baseline_partition.boundary_dropped,
            "development_range": [min(baseline_by_date).isoformat(), max(baseline_by_date).isoformat()],
        },
        "walk_forward_fold_ranges": [
            [fold.index, fold.test_sessions[0].isoformat(), fold.test_sessions[-1].isoformat()] for fold in folds
        ],
        "survivorship": coverage,
        "signals": results,
    }
    output.write_text(json.dumps(report, indent=2, default=str))
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Rerun rule-signal backtests through src/backtest")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-register", action="store_true")
    args = parser.parse_args()
    report = run_rerun(args.output, register=not args.no_register)
    print(json.dumps({k: report[k] for k in ("signals_tested", "baseline", "rows_meta")}, indent=2, default=str))


if __name__ == "__main__":
    main()
