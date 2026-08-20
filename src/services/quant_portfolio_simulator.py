from __future__ import annotations

import math
import random
from statistics import mean, median, pstdev
from typing import Any, Sequence

SIM_MAX_POSITIONS = 5
SIM_MAX_HOLDING_SESSIONS = 20
SIM_ROUND_TRIP_COST_PERCENT = 1.0
SIM_BLOCK_LENGTH = 20
SIM_BOOTSTRAP_ITERATIONS = 2000
SIM_BOOTSTRAP_SEED = 20260821


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


def _max_drawdown(curve: Sequence[float]) -> float | None:
    if not curve:
        return None
    peak = float(curve[0])
    worst = 0.0
    for value in curve:
        value = float(value)
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst * 100.0


def _asof_price(price_history: dict[str, dict[Any, float]], symbol: str, trading_date: Any, cache: dict[str, float]) -> float | None:
    exact = price_history.get(symbol, {}).get(trading_date)
    if exact is not None and float(exact) > 0:
        cache[symbol] = float(exact)
    return cache.get(symbol)


def simulate_rebalance_portfolio(
    *,
    market_dates: Sequence[Any],
    signals_by_date: dict[Any, Sequence[str]],
    price_history: dict[str, dict[Any, float]],
    max_positions: int = SIM_MAX_POSITIONS,
    max_holding_sessions: int = SIM_MAX_HOLDING_SESSIONS,
    round_trip_cost_percent: float = SIM_ROUND_TRIP_COST_PERCENT,
) -> dict[str, Any]:
    """Simulate equal-weight rebalancing on every available frozen decision date.

    Trades occur at the decision-date close. Only names selected by that day's
    frozen signal may be held. A position is force-closed after the configured
    market-session holding cap even when no new signal arrives that day.
    """
    dates = sorted(market_dates)
    if not dates:
        return {"status": "no_market_dates"}

    side_cost = max(0.0, float(round_trip_cost_percent)) / 200.0
    cash = 1.0
    shares: dict[str, float] = {}
    entry_index: dict[str, int] = {}
    last_prices: dict[str, float] = {}
    curve: list[float] = []
    daily_returns: list[float] = []
    dated_returns: list[dict[str, Any]] = []
    total_trade_notional = 0.0
    total_cost = 0.0
    buys = 0
    sells = 0
    completed_holding_sessions: list[int] = []
    active_position_counts: list[int] = []
    previous_wealth = 1.0

    def position_value(symbol: str, trading_date: Any) -> float:
        price = _asof_price(price_history, symbol, trading_date, last_prices)
        return float(shares.get(symbol, 0.0)) * float(price or 0.0)

    def portfolio_value(trading_date: Any) -> float:
        return cash + sum(position_value(symbol, trading_date) for symbol in list(shares))

    def sell(symbol: str, notional: float, trading_date: Any, *, fully_close: bool = False, date_index: int) -> None:
        nonlocal cash, total_trade_notional, total_cost, sells
        price = _asof_price(price_history, symbol, trading_date, last_prices)
        if price is None or price <= 0 or symbol not in shares:
            return
        current_value = shares[symbol] * price
        amount = min(max(0.0, notional), current_value)
        if amount <= 1e-12:
            return
        quantity = amount / price
        fee = amount * side_cost
        shares[symbol] -= quantity
        cash += amount - fee
        total_trade_notional += amount
        total_cost += fee
        sells += 1
        if fully_close or shares[symbol] * price <= 1e-10:
            shares.pop(symbol, None)
            started = entry_index.pop(symbol, date_index)
            completed_holding_sessions.append(max(0, date_index - started))

    def buy(symbol: str, notional: float, trading_date: Any, *, date_index: int) -> None:
        nonlocal cash, total_trade_notional, total_cost, buys
        price = _asof_price(price_history, symbol, trading_date, last_prices)
        if price is None or price <= 0:
            return
        amount = min(max(0.0, notional), cash / max(1e-12, 1.0 + side_cost))
        if amount <= 1e-12:
            return
        fee = amount * side_cost
        shares[symbol] = shares.get(symbol, 0.0) + amount / price
        cash -= amount + fee
        total_trade_notional += amount
        total_cost += fee
        buys += 1
        entry_index.setdefault(symbol, date_index)

    for date_index, trading_date in enumerate(dates):
        # Refresh marks, then enforce the holding cap whether or not a new model
        # decision exists on this session.
        portfolio_value(trading_date)
        expired = [
            symbol
            for symbol, started in list(entry_index.items())
            if date_index - started >= max_holding_sessions
        ]
        for symbol in expired:
            sell(symbol, position_value(symbol, trading_date), trading_date, fully_close=True, date_index=date_index)

        if trading_date in signals_by_date:
            requested: list[str] = []
            for symbol in signals_by_date[trading_date]:
                if symbol not in requested:
                    requested.append(symbol)
                if len(requested) >= max_positions:
                    break
            target = {
                symbol
                for symbol in requested
                if _asof_price(price_history, symbol, trading_date, last_prices) is not None
            }

            for symbol in list(shares):
                if symbol not in target:
                    sell(symbol, position_value(symbol, trading_date), trading_date, fully_close=True, date_index=date_index)

            if target:
                wealth_after_exits = portfolio_value(trading_date)
                target_value = wealth_after_exits / len(target)
                for symbol in sorted(target):
                    current = position_value(symbol, trading_date) if symbol in shares else 0.0
                    if current > target_value:
                        sell(symbol, current - target_value, trading_date, date_index=date_index)
                for symbol in sorted(target):
                    current = position_value(symbol, trading_date) if symbol in shares else 0.0
                    if current < target_value:
                        buy(symbol, target_value - current, trading_date, date_index=date_index)

        end_wealth = portfolio_value(trading_date)
        daily_return = end_wealth / previous_wealth - 1.0 if previous_wealth > 0 else 0.0
        daily_returns.append(daily_return)
        dated_returns.append({"date": trading_date, "return": daily_return})
        curve.append(end_wealth)
        active_position_counts.append(len(shares))
        previous_wealth = end_wealth

    # Charge the economically unavoidable terminal sell-side cost.
    if dates and shares:
        final_date = dates[-1]
        final_index = len(dates) - 1
        before = portfolio_value(final_date)
        for symbol in list(shares):
            sell(symbol, position_value(symbol, final_date), final_date, fully_close=True, date_index=final_index)
        after = cash
        if before > 0 and daily_returns:
            terminal_factor = after / before
            daily_returns[-1] = (1.0 + daily_returns[-1]) * terminal_factor - 1.0
            dated_returns[-1]["return"] = daily_returns[-1]
            curve[-1] = after
        previous_wealth = after

    years = max(1.0 / 252.0, len(dates) / 252.0)
    total_return = (previous_wealth - 1.0) * 100.0
    cagr = (previous_wealth ** (1.0 / years) - 1.0) * 100.0 if previous_wealth > 0 else -100.0
    volatility = pstdev(daily_returns) * math.sqrt(252.0) * 100.0 if len(daily_returns) >= 2 else None
    daily_mean = mean(daily_returns) if daily_returns else 0.0
    daily_std = pstdev(daily_returns) if len(daily_returns) >= 2 else 0.0
    sharpe_like = daily_mean / daily_std * math.sqrt(252.0) if daily_std > 0 else None

    return {
        "status": "simulated",
        "sessions": len(dates),
        "decision_dates": sum(date_value in signals_by_date for date_value in dates),
        "max_positions": max_positions,
        "max_holding_sessions": max_holding_sessions,
        "round_trip_cost_percent": round_trip_cost_percent,
        "ending_wealth": previous_wealth,
        "total_return_percent": total_return,
        "approx_cagr_percent": cagr,
        "annualized_volatility_percent": volatility,
        "sharpe_like": sharpe_like,
        "max_drawdown_percent": _max_drawdown(curve),
        "total_transaction_cost_percent_of_initial_capital": total_cost * 100.0,
        "cumulative_turnover_x_initial_capital": total_trade_notional,
        "annualized_turnover_x": total_trade_notional / years,
        "buy_transactions": buys,
        "sell_transactions": sells,
        "mean_positions": mean(active_position_counts) if active_position_counts else 0.0,
        "mean_completed_holding_sessions": mean(completed_holding_sessions) if completed_holding_sessions else None,
        "median_completed_holding_sessions": median(completed_holding_sessions) if completed_holding_sessions else None,
        "daily_returns": dated_returns,
        "equity_curve": [
            {"date": trading_date, "wealth": wealth}
            for trading_date, wealth in zip(dates, curve)
        ],
    }


def block_bootstrap_incremental_returns(
    strategy_a: Sequence[dict[str, Any]],
    strategy_b: Sequence[dict[str, Any]],
    *,
    block_length: int = SIM_BLOCK_LENGTH,
    iterations: int = SIM_BOOTSTRAP_ITERATIONS,
    seed: int = SIM_BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Moving-block bootstrap of paired daily strategy-return differences."""
    by_date_a = {row["date"]: float(row["return"]) for row in strategy_a}
    by_date_b = {row["date"]: float(row["return"]) for row in strategy_b}
    dates = sorted(set(by_date_a) & set(by_date_b))
    values = [by_date_a[trading_date] - by_date_b[trading_date] for trading_date in dates]
    if len(values) < max(10, block_length):
        return {
            "sessions": len(values),
            "block_length": block_length,
            "iterations": 0,
            "mean_daily_incremental_percent": mean(values) * 100.0 if values else None,
            "annualized_incremental_return_95": {"low": None, "high": None},
        }

    block_length = max(2, min(int(block_length), len(values)))
    starts = list(range(0, len(values) - block_length + 1))
    rng = random.Random(seed)
    annualized_samples: list[float] = []
    for _ in range(iterations):
        sample: list[float] = []
        while len(sample) < len(values):
            start = starts[rng.randrange(len(starts))]
            sample.extend(values[start : start + block_length])
        sample = sample[: len(values)]
        annualized_samples.append(mean(sample) * 252.0 * 100.0)

    return {
        "sessions": len(values),
        "block_length": block_length,
        "iterations": iterations,
        "mean_daily_incremental_percent": mean(values) * 100.0,
        "annualized_incremental_return_percent": mean(values) * 252.0 * 100.0,
        "annualized_incremental_return_95": {
            "low": _percentile(annualized_samples, 0.025),
            "high": _percentile(annualized_samples, 0.975),
        },
    }
