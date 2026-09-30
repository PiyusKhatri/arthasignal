from __future__ import annotations

import math
from collections import defaultdict
from statistics import mean, median
from typing import Any, Sequence

DIAGNOSTIC_POLICY_VERSION = "2026-08-21-e1-diagnostics-v1"
ROLLING_WINDOWS = (60, 120, 252)
BLOCK_LENGTH = 20


def _compound(returns: Sequence[float]) -> float:
    wealth = 1.0
    for value in returns:
        wealth *= 1.0 + float(value)
    return wealth - 1.0


def _max_drawdown_from_returns(returns: Sequence[float]) -> float | None:
    if not returns:
        return None
    wealth = 1.0
    peak = 1.0
    worst = 0.0
    for value in returns:
        wealth *= 1.0 + float(value)
        peak = max(peak, wealth)
        if peak > 0:
            worst = min(worst, wealth / peak - 1.0)
    return worst * 100.0


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = max(0.0, min(1.0, float(fraction))) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def baseline_rank_bucket(rank: int | None) -> str:
    if rank is None:
        return "unknown"
    if rank <= 5:
        return "top_5"
    if rank <= 10:
        return "rank_6_10"
    if rank <= 20:
        return "rank_11_20"
    return "rank_21_plus"


def holding_bucket(sessions: int) -> str:
    if sessions <= 2:
        return "0_2"
    if sessions <= 5:
        return "3_5"
    if sessions <= 10:
        return "6_10"
    if sessions <= 15:
        return "11_15"
    return "16_20_plus"


def prediction_metadata_index(predictions: Sequence[dict[str, Any]]) -> dict[tuple[Any, str], dict[str, Any]]:
    index: dict[tuple[Any, str], dict[str, Any]] = {}
    for candidate in predictions:
        row = candidate.get("row", {})
        trading_date = row.get("date")
        symbol = str(row.get("symbol") or "")
        if trading_date is None or not symbol:
            continue
        index[(trading_date, symbol)] = {
            "market_regime": str(candidate.get("market_regime") or row.get("v4_market_regime") or "unknown"),
            "sector_regime": str(candidate.get("sector_regime") or row.get("v4_sector_regime") or "unknown"),
            "sector": str(row.get("sector") or "unknown"),
            "liquidity_bucket": str(row.get("liquidity_bucket") or "unknown"),
            "override_action": str(candidate.get("override_action") or "hold"),
            "baseline_rank": int(row["baseline_rank"]) if row.get("baseline_rank") is not None else None,
            "baseline_rank_bucket": baseline_rank_bucket(
                int(row["baseline_rank"]) if row.get("baseline_rank") is not None else None
            ),
            "final_score": float(candidate.get("final_score") or 0.0),
            "baseline_score": float(row.get("baseline_score") or 0.0),
            "expected_excess_return_percent": float(candidate.get("expected_excess_return_percent") or 0.0),
            "predicted_mae_percent": float(candidate.get("predicted_mae_percent") or 0.0),
            "predicted_mfe_percent": float(candidate.get("predicted_mfe_percent") or 0.0),
        }
    return index


def reconstruct_completed_trades(
    trade_log: Sequence[dict[str, Any]],
    *,
    market_dates: Sequence[Any],
    price_history: dict[str, dict[Any, float]],
    predictions: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pair E1 buys/sells and attach entry-time OOS metadata.

    The diagnostic return is based on the same adjusted-close price history used
    by the execution simulator. It is explanatory only; portfolio accounting
    remains authoritative for total return and costs.
    """
    date_index = {trading_date: index for index, trading_date in enumerate(market_dates)}
    metadata = prediction_metadata_index(predictions)
    open_entries: dict[str, dict[str, Any]] = {}
    completed: list[dict[str, Any]] = []

    for event in trade_log:
        symbol = str(event.get("symbol") or "")
        trading_date = event.get("date")
        if not symbol or trading_date is None:
            continue
        if event.get("side") == "buy":
            price = price_history.get(symbol, {}).get(trading_date)
            open_entries[symbol] = {
                "entry_date": trading_date,
                "entry_price": float(price) if price is not None else None,
                "entry_reason": str(event.get("reason") or "entry"),
                "entry_score": float(event.get("score") or 0.0),
                "entry_notional": float(event.get("notional") or 0.0),
                "entry_fee": float(event.get("fee") or 0.0),
                **metadata.get((trading_date, symbol), {}),
            }
            continue
        if event.get("side") != "sell":
            continue
        entry = open_entries.pop(symbol, None)
        if entry is None:
            continue
        exit_price = price_history.get(symbol, {}).get(trading_date)
        if exit_price is None and event.get("stale_terminal_price"):
            prior = [
                price
                for date_value, price in price_history.get(symbol, {}).items()
                if date_value <= trading_date
            ]
            exit_price = prior[-1] if prior else None
        entry_price = entry.get("entry_price")
        gross_return = (
            (float(exit_price) / float(entry_price) - 1.0) * 100.0
            if exit_price is not None and entry_price not in (None, 0.0)
            else None
        )
        entry_i = date_index.get(entry["entry_date"])
        exit_i = date_index.get(trading_date)
        sessions = max(0, int(exit_i - entry_i)) if entry_i is not None and exit_i is not None else 0
        completed.append(
            {
                **entry,
                "symbol": symbol,
                "exit_date": trading_date,
                "exit_price": float(exit_price) if exit_price is not None else None,
                "exit_reason": str(event.get("reason") or "unknown"),
                "exit_notional": float(event.get("notional") or 0.0),
                "exit_fee": float(event.get("fee") or 0.0),
                "holding_sessions": sessions,
                "holding_bucket": holding_bucket(sessions),
                "gross_stock_return_percent": gross_return,
                "year": int(getattr(entry["entry_date"], "year", 0) or 0),
            }
        )
    return completed


def turnover_source_diagnostics(trade_log: Sequence[dict[str, Any]]) -> dict[str, Any]:
    sells = [event for event in trade_log if event.get("side") == "sell"]
    buys = [event for event in trade_log if event.get("side") == "buy"]
    sell_notional = sum(float(event.get("notional") or 0.0) for event in sells)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in sells:
        grouped[str(event.get("reason") or "unknown")].append(event)

    by_reason: dict[str, Any] = {}
    for reason, events in sorted(grouped.items()):
        notional = sum(float(event.get("notional") or 0.0) for event in events)
        fees = sum(float(event.get("fee") or 0.0) for event in events)
        by_reason[reason] = {
            "sell_count": len(events),
            "sell_notional_x_initial_capital": notional,
            "share_of_sell_notional": notional / sell_notional if sell_notional > 0 else None,
            "sell_fees_x_initial_capital": fees,
        }

    return {
        "buy_count": len(buys),
        "sell_count": len(sells),
        "buy_notional_x_initial_capital": sum(float(event.get("notional") or 0.0) for event in buys),
        "sell_notional_x_initial_capital": sell_notional,
        "by_exit_reason": by_reason,
    }


def holding_diagnostics(completed_trades: Sequence[dict[str, Any]]) -> dict[str, Any]:
    sessions = [int(row["holding_sessions"]) for row in completed_trades]
    by_reason: dict[str, list[int]] = defaultdict(list)
    for row in completed_trades:
        by_reason[str(row.get("exit_reason") or "unknown")].append(int(row["holding_sessions"]))
    return {
        "completed_trades": len(completed_trades),
        "mean_sessions": mean(sessions) if sessions else None,
        "median_sessions": median(sessions) if sessions else None,
        "p25_sessions": _percentile(sessions, 0.25),
        "p75_sessions": _percentile(sessions, 0.75),
        "by_exit_reason": {
            reason: {
                "trades": len(values),
                "mean_sessions": mean(values),
                "median_sessions": median(values),
            }
            for reason, values in sorted(by_reason.items())
        },
    }


def _aligned_returns(
    strategy_a: Sequence[dict[str, Any]],
    strategy_b: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    a = {row["date"]: float(row["return"]) for row in strategy_a}
    b = {row["date"]: float(row["return"]) for row in strategy_b}
    return [
        {"date": trading_date, "a": a[trading_date], "b": b[trading_date]}
        for trading_date in sorted(set(a) & set(b))
    ]


def rolling_performance_diagnostics(
    strategy_a: Sequence[dict[str, Any]],
    strategy_b: Sequence[dict[str, Any]],
    *,
    windows: Sequence[int] = ROLLING_WINDOWS,
) -> dict[str, Any]:
    aligned = _aligned_returns(strategy_a, strategy_b)
    output: dict[str, Any] = {}
    for window in windows:
        rows: list[dict[str, Any]] = []
        if len(aligned) >= window:
            for end in range(window, len(aligned) + 1):
                sample = aligned[end - window : end]
                a_returns = [row["a"] for row in sample]
                b_returns = [row["b"] for row in sample]
                a_compound = _compound(a_returns) * 100.0
                b_compound = _compound(b_returns) * 100.0
                rows.append(
                    {
                        "start": sample[0]["date"],
                        "end": sample[-1]["date"],
                        "strategy_return_percent": a_compound,
                        "baseline_return_percent": b_compound,
                        "incremental_return_percent": a_compound - b_compound,
                        "strategy_max_drawdown_percent": _max_drawdown_from_returns(a_returns),
                        "baseline_max_drawdown_percent": _max_drawdown_from_returns(b_returns),
                    }
                )
        increments = [float(row["incremental_return_percent"]) for row in rows]
        ordered = sorted(rows, key=lambda row: float(row["incremental_return_percent"]))
        output[str(window)] = {
            "windows": len(rows),
            "mean_incremental_return_percent": mean(increments) if increments else None,
            "median_incremental_return_percent": median(increments) if increments else None,
            "positive_window_rate": mean(1.0 if value > 0 else 0.0 for value in increments) if increments else None,
            "worst_5": ordered[:5],
            "best_5": ordered[-5:][::-1],
        }
    return output


def contiguous_block_diagnostics(
    strategy_a: Sequence[dict[str, Any]],
    strategy_b: Sequence[dict[str, Any]],
    *,
    block_length: int = BLOCK_LENGTH,
) -> dict[str, Any]:
    aligned = _aligned_returns(strategy_a, strategy_b)
    blocks: list[dict[str, Any]] = []
    for start in range(0, len(aligned), block_length):
        sample = aligned[start : start + block_length]
        if len(sample) < block_length:
            break
        a_return = _compound([row["a"] for row in sample]) * 100.0
        b_return = _compound([row["b"] for row in sample]) * 100.0
        blocks.append(
            {
                "start": sample[0]["date"],
                "end": sample[-1]["date"],
                "strategy_return_percent": a_return,
                "baseline_return_percent": b_return,
                "incremental_return_percent": a_return - b_return,
            }
        )
    ordered = sorted(blocks, key=lambda row: float(row["incremental_return_percent"]))
    increments = [float(row["incremental_return_percent"]) for row in blocks]
    return {
        "block_length": block_length,
        "blocks": len(blocks),
        "positive_block_rate": mean(1.0 if value > 0 else 0.0 for value in increments) if increments else None,
        "mean_incremental_return_percent": mean(increments) if increments else None,
        "median_incremental_return_percent": median(increments) if increments else None,
        "worst_10": ordered[:10],
        "best_10": ordered[-10:][::-1],
    }


def trade_breakdowns(completed_trades: Sequence[dict[str, Any]]) -> dict[str, Any]:
    dimensions = (
        "year",
        "market_regime",
        "sector",
        "sector_regime",
        "liquidity_bucket",
        "override_action",
        "baseline_rank_bucket",
        "holding_bucket",
        "exit_reason",
    )
    output: dict[str, Any] = {}
    for dimension in dimensions:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for trade in completed_trades:
            grouped[str(trade.get(dimension, "unknown"))].append(trade)
        output[dimension] = {}
        for key, trades in sorted(grouped.items()):
            returns = [
                float(trade["gross_stock_return_percent"])
                for trade in trades
                if trade.get("gross_stock_return_percent") is not None
            ]
            output[dimension][key] = {
                "trades": len(trades),
                "mean_gross_return_percent": mean(returns) if returns else None,
                "median_gross_return_percent": median(returns) if returns else None,
                "positive_trade_rate": mean(1.0 if value > 0 else 0.0 for value in returns) if returns else None,
                "mean_holding_sessions": mean(int(trade["holding_sessions"]) for trade in trades),
                "sell_notional_x_initial_capital": sum(float(trade.get("exit_notional") or 0.0) for trade in trades),
            }
    return output
