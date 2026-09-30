from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date
from statistics import mean
from typing import Any, Collection, Sequence

from src.backtest.baselines import (
    BASELINE_EQUAL_WEIGHT,
    BASELINE_MOMENTUM,
    BASELINE_NEPSE,
    equal_weight_universe_returns,
    nepse_buy_and_hold_returns,
    simple_momentum_returns,
)
from src.backtest.config import HoldoutConfig
from src.backtest.entry import assert_entry_after_signal
from src.backtest.stats import adjusted_alpha, clustered_mean_interval
from src.backtest.types import EXIT_BLOCKED, EXIT_STRANDED, Row


def _interval(values_by_date: dict[date, list[float]], alpha: float) -> dict[str, Any] | None:
    interval = clustered_mean_interval(values_by_date, alpha)
    return asdict(interval) if interval is not None else None


def selection_report(
    rows: Sequence[Row],
    selected_keys: Collection[tuple[str, date]],
    config: HoldoutConfig,
    variants_tried: int,
) -> dict[str, Any]:
    selected = set(selected_keys)
    by_date: dict[date, list[Row]] = defaultdict(list)
    for row in rows:
        if row.label is None:
            continue
        assert_entry_after_signal(row.label, row.signal_date)
        by_date[row.signal_date].append(row)

    model_gross: dict[date, list[float]] = {}
    baseline_returns: dict[str, dict[date, list[float]]] = {
        BASELINE_NEPSE: {},
        BASELINE_EQUAL_WEIGHT: {},
        BASELINE_MOMENTUM: {},
    }
    exit_statuses: Counter[str] = Counter()
    for signal_date in sorted(by_date):
        date_rows = by_date[signal_date]
        chosen = [row for row in date_rows if row.key in selected]
        if not chosen:
            continue
        model_gross[signal_date] = [float(row.label.gross_return) for row in chosen]
        exit_statuses.update(row.label.exit_status for row in chosen)
        baseline_returns[BASELINE_NEPSE][signal_date] = nepse_buy_and_hold_returns(chosen)
        baseline_returns[BASELINE_EQUAL_WEIGHT][signal_date] = equal_weight_universe_returns(date_rows)
        baseline_returns[BASELINE_MOMENTUM][signal_date] = simple_momentum_returns(date_rows, len(chosen))

    alpha = adjusted_alpha(config.base_alpha, variants_tried)
    selected_calls = sum(len(values) for values in model_gross.values())
    blocked = exit_statuses[EXIT_BLOCKED] + exit_statuses[EXIT_STRANDED]

    cost_sensitivity: list[dict[str, Any]] = []
    for cost in config.cost_levels_round_trip:
        model_net = {d: [value - cost for value in values] for d, values in model_gross.items()}
        baselines: dict[str, Any] = {}
        for name, per_date in baseline_returns.items():
            baseline_cost = 0.0 if name == BASELINE_NEPSE else cost
            differences: dict[date, list[float]] = {}
            baseline_net: dict[date, list[float]] = {}
            for d, values in per_date.items():
                if not values:
                    continue
                baseline_net[d] = [value - baseline_cost for value in values]
                differences[d] = [mean(model_net[d]) - mean(baseline_net[d])]
            baselines[name] = {
                "round_trip_cost": baseline_cost,
                "net_return": _interval(baseline_net, alpha),
                "model_minus_baseline": _interval(differences, alpha),
            }
        cost_sensitivity.append(
            {
                "round_trip_cost": cost,
                "model_net_return": _interval(model_net, alpha),
                "baselines": baselines,
            }
        )

    return {
        "config_version": config.version,
        "selected_calls": selected_calls,
        "active_dates": len(model_gross),
        "variants_tried": variants_tried,
        "base_alpha": config.base_alpha,
        "alpha_after_multiple_testing_penalty": alpha,
        "interval_method": "normal approximation over signal-date cluster means",
        "entry_rule": "next session open after the signal date",
        "exit_statuses": dict(exit_statuses),
        "blocked_sell_rate": blocked / selected_calls if selected_calls else None,
        "cost_sensitivity": cost_sensitivity,
    }
