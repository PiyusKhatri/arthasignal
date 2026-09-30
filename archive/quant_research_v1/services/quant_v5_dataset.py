"""Pure point-in-time dataset alignment for the V5 research challenger.

The contract deliberately separates a completed signal session from its label:
the signal is observed at the close of market session D, entry is the exact D+1
stock open, and exit is the first stock close from D+20 through D+23.  Returns
for the stock and NEPSE use those same entry and exit sessions.

``signal_rows`` are intentionally treated as outcome-free, point-in-time
candidates. Active-equity and listing eligibility belong to the upstream
signal-row builder; this label aligner never infers them from current status.
"""

from __future__ import annotations

import math
from copy import deepcopy
from statistics import median, pstdev
from typing import Any, Mapping, Sequence

DEFAULT_HORIZON_SESSIONS = 20
DEFAULT_EXIT_GRACE_SESSIONS = 3
DAILY_STEP = 1
ROUND_TRIP_COST_PERCENT = 1.0
SEVERE_MAE_THRESHOLD_PERCENT = 5.0

Row = Mapping[str, Any]
StockBars = Mapping[str, Sequence[Row]] | Sequence[Row]


def _positive_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0.0 else None


def _optional_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _row_date(row: Row, *, action: bool = False) -> Any:
    return row.get("action_date") if action else row.get("date")


def _rows_by_symbol(rows: StockBars | Sequence[Row]) -> dict[str, list[Row]]:
    if isinstance(rows, Mapping):
        return {
            str(symbol): sorted(list(symbol_rows), key=lambda row: _row_date(row))
            for symbol, symbol_rows in rows.items()
        }

    grouped: dict[str, list[Row]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "")
        if symbol:
            grouped.setdefault(symbol, []).append(row)
    for symbol_rows in grouped.values():
        symbol_rows.sort(key=lambda row: _row_date(row))
    return grouped


def _history_by_symbol(
    rows: Mapping[str, Sequence[Row]] | Sequence[Row],
    *,
    action: bool = False,
) -> dict[str, list[Row]]:
    if isinstance(rows, Mapping):
        grouped = {str(symbol): list(symbol_rows) for symbol, symbol_rows in rows.items()}
    else:
        grouped: dict[str, list[Row]] = {}
        for row in rows:
            symbol = str(row.get("symbol") or "")
            if symbol:
                grouped.setdefault(symbol, []).append(row)
    for symbol_rows in grouped.values():
        symbol_rows.sort(key=lambda row: _row_date(row, action=action))
    return grouped


def _bar_index(rows: Sequence[Row], *, cutoff: Any | None = None) -> dict[Any, Row]:
    return {
        row.get("date"): row
        for row in rows
        if row.get("date") is not None and (cutoff is None or row["date"] <= cutoff)
    }


def _candidate_flag(row: Row) -> bool:
    for key in ("is_candidate", "candidate_member", "selected", "selected_baseline"):
        if key in row:
            return bool(row[key])
    return False


def _stability_features(
    *,
    signal: Row,
    symbol_signals: Sequence[Row],
    membership_rows: Sequence[Row],
    stock_by_date: Mapping[Any, Row],
    trailing_market_dates: Sequence[Any],
) -> dict[str, float | int | None] | None:
    signal_date = signal["date"]
    available = 0
    turnovers: list[float] = []
    for session in trailing_market_dates:
        bar = stock_by_date.get(session)
        turnover = _positive_number(bar.get("turnover")) if bar else None
        is_trade = bool(
            bar
            and turnover is not None
            and _positive_number(bar.get("open")) is not None
            and _positive_number(bar.get("close")) is not None
        )
        if is_trade:
            available += 1
        turnovers.append(turnover if is_trade and turnover is not None else 0.0)

    trade_availability = available / len(trailing_market_dates) if trailing_market_dates else 0.0
    turnover_mean = sum(turnovers) / len(turnovers) if turnovers else 0.0
    if turnover_mean <= 0.0:
        return None
    turnover_cv = pstdev(turnovers) / turnover_mean if len(turnovers) > 1 else 0.0

    prior_market_dates = set(trailing_market_dates[-6:-1])
    lagged_percentiles = [
        value
        for row in symbol_signals
        if row.get("date") in prior_market_dates and row["date"] < signal_date
        if (value := _optional_number(row.get("baseline_percentile"))) is not None
    ]

    membership_dates = prior_market_dates
    membership_count = sum(
        1
        for row in membership_rows
        if row.get("date") in membership_dates
        and row["date"] < signal_date
        and _candidate_flag(row)
    )

    return {
        "trade_availability_20": trade_availability,
        "turnover_cv_20": turnover_cv,
        "turnover_percentile": _optional_number(signal.get("turnover_percentile")),
        "baseline_percentile_median_5": median(lagged_percentiles) if lagged_percentiles else 0.50,
        "candidate_membership_count_5": membership_count,
    }


def _has_corporate_action(actions: Sequence[Row], entry_date: Any, exit_date: Any) -> bool:
    return any(
        (action_date := _row_date(row, action=True)) is not None
        and entry_date <= action_date <= exit_date
        for row in actions
    )


def _close_path_excursions(
    *,
    stock_by_date: Mapping[Any, Row],
    path_dates: Sequence[Any],
    entry_price: float,
) -> tuple[float, float, float, bool]:
    path_returns = [0.0]
    for session in path_dates:
        bar = stock_by_date.get(session)
        close = _positive_number(bar.get("close")) if bar else None
        if close is not None:
            path_returns.append((close / entry_price - 1.0) * 100.0)
    max_adverse = min(path_returns)
    max_favorable = max(path_returns)
    mae_magnitude = max(0.0, -max_adverse)
    return (
        max_adverse,
        max_favorable,
        mae_magnitude,
        mae_magnitude >= SEVERE_MAE_THRESHOLD_PERCENT,
    )


def build_v5_dataset_with_diagnostics(
    *,
    signal_rows: Sequence[Row],
    stock_bars: StockBars,
    market_bars: Sequence[Row],
    corporate_actions: Mapping[str, Sequence[Row]] | Sequence[Row] = (),
    membership_history: Mapping[str, Sequence[Row]] | Sequence[Row] = (),
    baseline_history: Mapping[str, Sequence[Row]] | Sequence[Row] | None = None,
    as_of_date: Any | None = None,
    horizon_sessions: int = DEFAULT_HORIZON_SESSIONS,
    exit_grace_sessions: int = DEFAULT_EXIT_GRACE_SESSIONS,
    step: int = DAILY_STEP,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Build labeled rows and counters without mutating any caller-owned input.

    Unexecutable observations are intentionally omitted instead of receiving a
    synthetic label. ``as_of_date`` is a hard data-availability cutoff, making
    the result invariant to records appended after that date.
    """
    if step != DAILY_STEP:
        raise ValueError("V5 dataset contract requires daily step=1 semantics")
    if horizon_sessions < 1:
        raise ValueError("horizon_sessions must be positive")
    if exit_grace_sessions < 0:
        raise ValueError("exit_grace_sessions cannot be negative")

    market_rows = sorted(
        (
            row
            for row in market_bars
            if row.get("date") is not None
            and (as_of_date is None or row["date"] <= as_of_date)
        ),
        key=lambda row: row["date"],
    )
    market_by_date = {row["date"]: row for row in market_rows}
    market_dates = list(market_by_date)
    market_position = {session: index for index, session in enumerate(market_dates)}

    stocks = _rows_by_symbol(stock_bars)
    actions = _history_by_symbol(corporate_actions, action=True)
    memberships = _history_by_symbol(membership_history)
    signals_by_symbol = _history_by_symbol(
        signal_rows if baseline_history is None else baseline_history
    )
    ordered_signals = sorted(
        (
            row
            for row in signal_rows
            if row.get("date") is not None
            and (as_of_date is None or row["date"] <= as_of_date)
        ),
        key=lambda row: (row["date"], str(row.get("symbol") or "")),
    )

    diagnostics = {
        "attempted": 0,
        "labeled": 0,
        "missing_entry": 0,
        "missing_exit": 0,
        "corporate_action_void": 0,
        "invalid_stability": 0,
    }
    result: list[dict[str, Any]] = []

    for signal in ordered_signals:
        diagnostics["attempted"] += 1
        symbol = str(signal.get("symbol") or "")
        signal_date = signal["date"]
        signal_position = market_position.get(signal_date)
        if signal_position is None or signal_position + 1 >= len(market_dates):
            diagnostics["missing_entry"] += 1
            continue

        entry_date = market_dates[signal_position + 1]
        market_entry = market_by_date[entry_date]
        stock_by_date = _bar_index(stocks.get(symbol, ()), cutoff=as_of_date)
        stock_entry = stock_by_date.get(entry_date)
        entry_price = _positive_number(stock_entry.get("open")) if stock_entry else None
        market_entry_price = _positive_number(market_entry.get("open"))
        if entry_price is None or market_entry_price is None:
            diagnostics["missing_entry"] += 1
            continue

        target_position = signal_position + horizon_sessions
        if target_position >= len(market_dates):
            diagnostics["missing_exit"] += 1
            continue
        target_date = market_dates[target_position]

        chosen_exit: tuple[Any, float, float, int] | None = None
        last_exit_position = min(
            len(market_dates) - 1,
            target_position + exit_grace_sessions,
        )
        for exit_position in range(target_position, last_exit_position + 1):
            exit_date = market_dates[exit_position]
            stock_exit = stock_by_date.get(exit_date)
            stock_exit_price = _positive_number(stock_exit.get("close")) if stock_exit else None
            market_exit_price = _positive_number(market_by_date[exit_date].get("close"))
            if stock_exit_price is not None and market_exit_price is not None:
                chosen_exit = (
                    exit_date,
                    stock_exit_price,
                    market_exit_price,
                    exit_position - target_position,
                )
                break

        if chosen_exit is None:
            diagnostics["missing_exit"] += 1
            continue
        exit_date, exit_price, market_exit_price, grace_sessions = chosen_exit

        if _has_corporate_action(actions.get(symbol, ()), entry_date, exit_date):
            diagnostics["corporate_action_void"] += 1
            continue

        stock_return = (exit_price / entry_price - 1.0) * 100.0
        market_return = (market_exit_price / market_entry_price - 1.0) * 100.0
        trailing_start = max(0, signal_position - 19)
        stability = _stability_features(
            signal=signal,
            symbol_signals=signals_by_symbol.get(symbol, ()),
            membership_rows=memberships.get(symbol, ()),
            stock_by_date=stock_by_date,
            trailing_market_dates=market_dates[trailing_start : signal_position + 1],
        )
        if stability is None:
            diagnostics["invalid_stability"] += 1
            continue

        exit_position = target_position + grace_sessions
        max_adverse, max_favorable, mae_magnitude, severe_mae = _close_path_excursions(
            stock_by_date=stock_by_date,
            path_dates=market_dates[signal_position + 1 : exit_position + 1],
            entry_price=entry_price,
        )
        excess_return = stock_return - market_return
        net_alpha = excess_return - ROUND_TRIP_COST_PERCENT
        success_after_cost = net_alpha > 1e-12

        output = deepcopy(dict(signal))
        output.update(
            {
                "symbol": symbol,
                "date": signal_date,
                "signal_date": signal_date,
                "entry_date": entry_date,
                "entry_price": entry_price,
                "target_date": target_date,
                "exit_date": exit_date,
                "exit_price": exit_price,
                "exit_grace_sessions": grace_sessions,
                "stock_return_percent": stock_return,
                "market_return_percent": market_return,
                "excess_return_percent": excess_return,
                "net_alpha_percent": net_alpha,
                "round_trip_cost_percent": ROUND_TRIP_COST_PERCENT,
                "success_after_cost": success_after_cost,
                "success": success_after_cost,
                "max_adverse_percent": max_adverse,
                "max_favorable_percent": max_favorable,
                "mae_magnitude_percent": mae_magnitude,
                "severe_mae": severe_mae,
                "label_end_date": exit_date,
                "stability_features": stability,
            }
        )
        result.append(output)
        diagnostics["labeled"] += 1

    return result, diagnostics


def build_v5_dataset(
    *,
    signal_rows: Sequence[Row],
    stock_bars: StockBars,
    market_bars: Sequence[Row],
    corporate_actions: Mapping[str, Sequence[Row]] | Sequence[Row] = (),
    membership_history: Mapping[str, Sequence[Row]] | Sequence[Row] = (),
    baseline_history: Mapping[str, Sequence[Row]] | Sequence[Row] | None = None,
    as_of_date: Any | None = None,
    horizon_sessions: int = DEFAULT_HORIZON_SESSIONS,
    exit_grace_sessions: int = DEFAULT_EXIT_GRACE_SESSIONS,
    step: int = DAILY_STEP,
) -> list[dict[str, Any]]:
    """Return only valid V5 labeled observations."""
    rows, _ = build_v5_dataset_with_diagnostics(
        signal_rows=signal_rows,
        stock_bars=stock_bars,
        market_bars=market_bars,
        corporate_actions=corporate_actions,
        membership_history=membership_history,
        baseline_history=baseline_history,
        as_of_date=as_of_date,
        horizon_sessions=horizon_sessions,
        exit_grace_sessions=exit_grace_sessions,
        step=step,
    )
    return rows


__all__ = [
    "DAILY_STEP",
    "DEFAULT_EXIT_GRACE_SESSIONS",
    "DEFAULT_HORIZON_SESSIONS",
    "ROUND_TRIP_COST_PERCENT",
    "SEVERE_MAE_THRESHOLD_PERCENT",
    "build_v5_dataset",
    "build_v5_dataset_with_diagnostics",
]
