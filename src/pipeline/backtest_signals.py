from __future__ import annotations

import itertools
import statistics
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import select

from src.database.connection import get_session
from src.database.models import DailyPrice, SignalTimeframe, TechnicalSignal

DEFAULT_FORWARD_DAYS = [5, 10, 20]
MAX_EXAMPLES_PER_HORIZON = 5

SignalConditionFn = Callable[[TechnicalSignal, "Decimal | None", "TechnicalSignal | None", "Decimal | None"], bool]


def _load_price_series() -> dict[str, dict[str, Any]]:
    with get_session() as session:
        rows = session.execute(
            select(DailyPrice.symbol, DailyPrice.date, DailyPrice.adjusted_close, DailyPrice.close)
            .order_by(DailyPrice.symbol, DailyPrice.date)
        ).all()

    series: dict[str, dict[str, Any]] = {}
    for symbol, group in itertools.groupby(rows, key=lambda r: r.symbol):
        group_rows = list(group)
        dates = [r.date for r in group_rows]
        closes = [r.adjusted_close if r.adjusted_close is not None else r.close for r in group_rows]
        series[symbol] = {
            "dates": dates,
            "closes": closes,
            "index_by_date": {d: i for i, d in enumerate(dates)},
        }
    return series


def _load_daily_signal_entries() -> list[tuple[TechnicalSignal, Decimal | None]]:
    with get_session() as session:
        rows = session.execute(
            select(TechnicalSignal, DailyPrice.close)
            .join(
                DailyPrice,
                (DailyPrice.symbol == TechnicalSignal.symbol) & (DailyPrice.date == TechnicalSignal.date),
            )
            .where(TechnicalSignal.timeframe == SignalTimeframe.DAILY)
            .order_by(TechnicalSignal.symbol, TechnicalSignal.date)
        ).all()
        entries = [(r[0], r[1]) for r in rows]
        session.expunge_all()
    return entries


def _evaluate_signal(
    signal_name: str,
    signal_condition_fn: SignalConditionFn,
    forward_days: list[int],
    signal_entries: list[tuple[TechnicalSignal, Decimal | None]],
    price_series: dict[str, dict[str, Any]],
    dedup_episodes: bool = True,
) -> dict[str, Any]:
    returns_by_horizon: dict[int, list[Decimal]] = {n: [] for n in forward_days}
    examples_by_horizon: dict[int, list[dict[str, Any]]] = {n: [] for n in forward_days}

    condition_active_by_symbol: dict[str, bool] = {}
    prev_entry_by_symbol: dict[str, tuple[TechnicalSignal, Decimal | None]] = {}
    episode_count = 0
    triggered_day_count = 0

    for row, close in signal_entries:
        prev_row, prev_close = prev_entry_by_symbol.get(row.symbol, (None, None))
        condition_true = signal_condition_fn(row, close, prev_row, prev_close)
        prev_entry_by_symbol[row.symbol] = (row, close)

        was_active = condition_active_by_symbol.get(row.symbol, False)
        condition_active_by_symbol[row.symbol] = condition_true

        if not condition_true:
            continue

        triggered_day_count += 1
        if dedup_episodes and was_active:
            continue
        episode_count += 1

        series = price_series.get(row.symbol)
        if series is None:
            continue

        idx = series["index_by_date"].get(row.date)
        if idx is None:
            continue

        current_close = series["closes"][idx]
        if current_close is None or current_close == 0:
            continue

        for n in forward_days:
            target_idx = idx + n
            if target_idx >= len(series["closes"]):
                continue

            future_close = series["closes"][target_idx]
            if future_close is None:
                continue

            forward_return = (future_close - current_close) / current_close * Decimal(100)
            returns_by_horizon[n].append(forward_return)

            if len(examples_by_horizon[n]) < MAX_EXAMPLES_PER_HORIZON:
                examples_by_horizon[n].append(
                    {
                        "symbol": row.symbol,
                        "signal_date": row.date,
                        "forward_date": series["dates"][target_idx],
                        "current_close": current_close,
                        "future_close": future_close,
                        "forward_return_pct": forward_return,
                    }
                )

    horizon_stats: dict[int, dict[str, Any]] = {}
    for n in forward_days:
        returns = returns_by_horizon[n]
        sample_size = len(returns)

        if sample_size == 0:
            horizon_stats[n] = {
                "sample_size": 0,
                "mean_forward_return": None,
                "median_forward_return": None,
                "win_rate": None,
                "stddev_forward_return": None,
                "examples": [],
            }
            continue

        wins = sum(1 for r in returns if r > 0)
        horizon_stats[n] = {
            "sample_size": sample_size,
            "mean_forward_return": statistics.mean(returns),
            "median_forward_return": statistics.median(returns),
            "win_rate": Decimal(wins) / Decimal(sample_size) * Decimal(100),
            "stddev_forward_return": statistics.stdev(returns) if sample_size > 1 else Decimal(0),
            "examples": examples_by_horizon[n],
        }

    return {
        "signal_name": signal_name,
        "forward_days": horizon_stats,
        "episode_count": episode_count,
        "triggered_day_count": triggered_day_count,
    }


def backtest_indicator_signal(
    signal_name: str,
    signal_condition_fn: SignalConditionFn,
    forward_days: list[int] = DEFAULT_FORWARD_DAYS,
    dedup_episodes: bool = True,
) -> dict[str, Any]:
    price_series = _load_price_series()
    signal_entries = _load_daily_signal_entries()
    return _evaluate_signal(
        signal_name, signal_condition_fn, forward_days, signal_entries, price_series, dedup_episodes
    )


def backtest_multiple_signals(
    signal_conditions: dict[str, SignalConditionFn],
    forward_days: list[int] = DEFAULT_FORWARD_DAYS,
    dedup_episodes: bool = True,
) -> dict[str, dict[str, Any]]:
    price_series = _load_price_series()
    signal_entries = _load_daily_signal_entries()

    results = {}
    for signal_name, condition_fn in signal_conditions.items():
        results[signal_name] = _evaluate_signal(
            signal_name, condition_fn, forward_days, signal_entries, price_series, dedup_episodes
        )
    return results
