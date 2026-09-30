from __future__ import annotations

import math
from statistics import mean, median, pstdev
from typing import Any, Sequence

from archive.quant_research_v1.services.quant_residual_alpha import V4_EXECUTION_HURDLE_PERCENT, V4_FINAL_CAPACITY

E1_POLICY_VERSION = "2026-08-21-execution-e1-v1"
E1_MAX_POSITIONS = 5
E1_MAX_HOLDING_SESSIONS = 20
E1_ROUND_TRIP_COST_PERCENT = 1.0
E1_REPLACEMENT_SCORE_MARGIN = 0.12


def _exact_price(price_history: dict[str, dict[Any, float]], symbol: str, trading_date: Any) -> float | None:
    value = price_history.get(symbol, {}).get(trading_date)
    return float(value) if value is not None and float(value) > 0 else None


def _asof_price(
    price_history: dict[str, dict[Any, float]],
    symbol: str,
    trading_date: Any,
    cache: dict[str, float],
) -> float | None:
    exact = _exact_price(price_history, symbol, trading_date)
    if exact is not None:
        cache[symbol] = exact
    return cache.get(symbol)


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


def _candidate_symbol(candidate: dict[str, Any]) -> str:
    return str(candidate["row"]["symbol"])


def _candidate_score(candidate: dict[str, Any], mode: str) -> float:
    if mode == "v41":
        return float(candidate.get("final_score") or 0.0)
    if mode == "baseline":
        return float(candidate["row"].get("baseline_percentile") or 0.0)
    raise ValueError(f"Unsupported E1 mode: {mode}")


def _hold_eligible(candidate: dict[str, Any], mode: str) -> bool:
    market = str(candidate.get("market_regime") or candidate["row"].get("v4_market_regime") or "")
    if V4_FINAL_CAPACITY.get(market, 0) <= 0:
        return False
    if mode == "v41" and candidate.get("override_action") == "reject":
        return False
    return True


def _entry_eligible(candidate: dict[str, Any], mode: str) -> bool:
    if not _hold_eligible(candidate, mode):
        return False
    if mode == "baseline":
        return True
    # Preserve V4.1's own new-entry qualification. E1 changes turnover policy,
    # not the predictive model's definition of a qualified new setup.
    expected = float(candidate.get("expected_excess_return_percent") or 0.0)
    return expected > V4_EXECUTION_HURDLE_PERCENT or candidate.get("override_action") == "promote"


def group_predictions_by_date(predictions: Sequence[dict[str, Any]]) -> dict[Any, list[dict[str, Any]]]:
    grouped: dict[Any, list[dict[str, Any]]] = {}
    for candidate in predictions:
        trading_date = candidate["row"]["date"]
        grouped.setdefault(trading_date, []).append(candidate)
    return grouped


def simulate_execution_policy_e1(
    *,
    market_dates: Sequence[Any],
    predictions_by_date: dict[Any, Sequence[dict[str, Any]]],
    price_history: dict[str, dict[Any, float]],
    observed_symbols_by_date: dict[Any, set[str]] | None = None,
    mode: str = "v41",
    max_positions: int = E1_MAX_POSITIONS,
    max_holding_sessions: int = E1_MAX_HOLDING_SESSIONS,
    round_trip_cost_percent: float = E1_ROUND_TRIP_COST_PERCENT,
    replacement_score_margin: float = E1_REPLACEMENT_SCORE_MARGIN,
    execution_price_history: dict[str, dict[Any, float]] | None = None,
    terminal_liquidation: bool = True,
) -> dict[str, Any]:
    """Sticky execution layer for frozen V4.1 or its transparent baseline.

    E1 deliberately does not change model predictions. Existing holdings survive
    normal rank drift while they remain in the current candidate set and are not
    rejected. Replacement requires a fixed score advantage, and surviving
    positions are never routinely rebalanced back to equal weight.

    Historical V4.1 rows were sampled every five stock observations. When
    `observed_symbols_by_date` is supplied, a missing candidate is treated as an
    ineligibility signal only if that stock actually had a fresh OOS observation
    on the date. With complete forward data, omit this argument and ordinary
    candidate absence is treated as a real loss of eligibility.

    `price_history` is the mark-to-market series. By default it is also the
    execution series, preserving the frozen E1 historical study. Forward replay
    can supply `execution_price_history` (for example next-session opens) without
    changing the E1 ranking, hold, exit, sizing, or replacement rules.
    """
    dates = sorted(market_dates)
    if not dates:
        return {"status": "no_market_dates", "policy_version": E1_POLICY_VERSION}
    if mode not in {"v41", "baseline"}:
        raise ValueError("mode must be 'v41' or 'baseline'")

    sampling_aware = observed_symbols_by_date is not None
    observed_symbols_by_date = observed_symbols_by_date or {}
    execution_prices = execution_price_history or price_history
    side_cost = max(0.0, float(round_trip_cost_percent)) / 200.0
    cash = 1.0
    shares: dict[str, float] = {}
    entry_index: dict[str, int] = {}
    last_prices: dict[str, float] = {}
    last_scores: dict[str, float] = {}
    curve: list[float] = []
    daily_returns: list[float] = []
    dated_returns: list[dict[str, Any]] = []
    active_position_counts: list[int] = []
    holding_sessions: list[int] = []
    trade_log: list[dict[str, Any]] = []

    total_notional = 0.0
    total_cost = 0.0
    buys = 0
    sells = 0
    replacements = 0
    blocked_buys = 0
    blocked_sells = 0
    exits_expired = 0
    exits_ineligible = 0
    exits_reject = 0
    sampled_absence_holds = 0
    previous_wealth = 1.0

    def mark_value(symbol: str, trading_date: Any) -> float:
        price = _asof_price(price_history, symbol, trading_date, last_prices)
        return shares.get(symbol, 0.0) * float(price or 0.0)

    def portfolio_value(trading_date: Any) -> float:
        return cash + sum(mark_value(symbol, trading_date) for symbol in list(shares))

    def sell(symbol: str, trading_date: Any, *, date_index: int, reason: str, allow_stale: bool = False) -> float:
        nonlocal cash, total_notional, total_cost, sells, blocked_sells
        nonlocal exits_expired, exits_ineligible, exits_reject
        price = _exact_price(execution_prices, symbol, trading_date)
        stale = False
        if price is None and allow_stale:
            price = _asof_price(price_history, symbol, trading_date, last_prices)
            stale = price is not None
        if price is None or symbol not in shares:
            blocked_sells += 1
            return 0.0
        notional = shares[symbol] * price
        fee = notional * side_cost
        cash += notional - fee
        total_notional += notional
        total_cost += fee
        sells += 1
        started = entry_index.pop(symbol, date_index)
        holding_sessions.append(max(0, date_index - started))
        shares.pop(symbol, None)
        last_scores.pop(symbol, None)
        if reason == "expired":
            exits_expired += 1
        elif reason == "reject":
            exits_reject += 1
        elif reason == "ineligible":
            exits_ineligible += 1
        trade_log.append(
            {
                "date": trading_date,
                "side": "sell",
                "symbol": symbol,
                "reason": reason,
                "notional": notional,
                "fee": fee,
                "execution_price": price,
                "stale_terminal_price": stale,
            }
        )
        return notional - fee

    def buy(
        symbol: str,
        trading_date: Any,
        *,
        date_index: int,
        desired_notional: float,
        reason: str,
        score: float,
    ) -> bool:
        nonlocal cash, total_notional, total_cost, buys, blocked_buys
        price = _exact_price(execution_prices, symbol, trading_date)
        if price is None:
            blocked_buys += 1
            return False
        mark = _exact_price(price_history, symbol, trading_date)
        if mark is not None:
            last_prices[symbol] = mark
        amount = min(max(0.0, desired_notional), cash / max(1e-12, 1.0 + side_cost))
        if amount <= 1e-12:
            return False
        fee = amount * side_cost
        shares[symbol] = amount / price
        entry_index[symbol] = date_index
        last_scores[symbol] = score
        cash -= amount + fee
        total_notional += amount
        total_cost += fee
        buys += 1
        trade_log.append(
            {
                "date": trading_date,
                "side": "buy",
                "symbol": symbol,
                "reason": reason,
                "notional": amount,
                "fee": fee,
                "execution_price": price,
                "score": score,
            }
        )
        return True

    for date_index, trading_date in enumerate(dates):
        portfolio_value(trading_date)

        # The 20-session holding cap is enforced every NEPSE session, independent
        # of whether a fresh V4.1 research decision exists on this date.
        expired = [
            symbol
            for symbol, started in list(entry_index.items())
            if date_index - started >= max_holding_sessions
        ]
        for symbol in expired:
            sell(symbol, trading_date, date_index=date_index, reason="expired")

        has_prediction_date = trading_date in predictions_by_date
        has_observation_date = sampling_aware and trading_date in observed_symbols_by_date
        if has_prediction_date or has_observation_date:
            date_candidates = list(predictions_by_date.get(trading_date, []))
            candidate_map = {_candidate_symbol(candidate): candidate for candidate in date_candidates}
            observed_today = observed_symbols_by_date.get(trading_date, set())

            # Refresh scores only when a fresh candidate observation exists.
            for symbol in list(shares):
                candidate = candidate_map.get(symbol)
                if candidate is not None:
                    last_scores[symbol] = _candidate_score(candidate, mode)
                    if mode == "v41" and candidate.get("override_action") == "reject":
                        sell(symbol, trading_date, date_index=date_index, reason="reject")
                        continue
                    if not _hold_eligible(candidate, mode):
                        sell(symbol, trading_date, date_index=date_index, reason="ineligible")
                        continue
                elif not sampling_aware or symbol in observed_today:
                    # Complete forward data: absence is genuine. Historical sparse
                    # data: exit only when a fresh row proves the name failed the filter.
                    sell(symbol, trading_date, date_index=date_index, reason="ineligible")
                else:
                    sampled_absence_holds += 1

            ranked_entries = sorted(
                (
                    candidate
                    for candidate in date_candidates
                    if _entry_eligible(candidate, mode) and _candidate_symbol(candidate) not in shares
                ),
                key=lambda candidate: _candidate_score(candidate, mode),
                reverse=True,
            )

            # Fill empty slots, targeting one fifth of current capital per new slot.
            # Existing holdings are left untouched and allowed to drift.
            for candidate in list(ranked_entries):
                if len(shares) >= max_positions:
                    break
                symbol = _candidate_symbol(candidate)
                score = _candidate_score(candidate, mode)
                wealth = portfolio_value(trading_date)
                target_slot = wealth / max_positions
                if buy(
                    symbol,
                    trading_date,
                    date_index=date_index,
                    desired_notional=target_slot,
                    reason="entry",
                    score=score,
                ):
                    ranked_entries.remove(candidate)

            # When full, replacement requires a material predeclared score gap.
            # Last valid scores are used for holdings without a fresh sampled row.
            while len(shares) >= max_positions and ranked_entries:
                scored_holdings = [
                    (float(last_scores[symbol]), symbol)
                    for symbol in shares
                    if symbol in last_scores
                ]
                if not scored_holdings:
                    break
                weakest_score, weakest_symbol = min(scored_holdings)

                challenger = ranked_entries[0]
                challenger_symbol = _candidate_symbol(challenger)
                challenger_score = _candidate_score(challenger, mode)
                if challenger_score < weakest_score + replacement_score_margin:
                    break

                if _exact_price(execution_prices, weakest_symbol, trading_date) is None:
                    blocked_sells += 1
                    break
                if _exact_price(execution_prices, challenger_symbol, trading_date) is None:
                    blocked_buys += 1
                    ranked_entries.pop(0)
                    continue

                proceeds = sell(
                    weakest_symbol,
                    trading_date,
                    date_index=date_index,
                    reason="replacement",
                )
                if proceeds <= 0.0:
                    break
                bought = buy(
                    challenger_symbol,
                    trading_date,
                    date_index=date_index,
                    desired_notional=min(proceeds, portfolio_value(trading_date) / max_positions),
                    reason="replacement",
                    score=challenger_score,
                )
                if bought:
                    replacements += 1
                ranked_entries.pop(0)

        end_wealth = portfolio_value(trading_date)
        daily_return = end_wealth / previous_wealth - 1.0 if previous_wealth > 0 else 0.0
        daily_returns.append(daily_return)
        dated_returns.append({"date": trading_date, "return": daily_return})
        curve.append(end_wealth)
        active_position_counts.append(len(shares))
        previous_wealth = end_wealth

    # Historical validation closes inventory for comparable terminal wealth.
    # Forward shadow replay disables this so open positions remain open evidence.
    terminal_stale_liquidations = 0
    if terminal_liquidation and dates and shares:
        final_date = dates[-1]
        final_index = len(dates) - 1
        before = portfolio_value(final_date)
        for symbol in list(shares):
            exact = _exact_price(execution_prices, symbol, final_date)
            if exact is None:
                terminal_stale_liquidations += 1
            sell(symbol, final_date, date_index=final_index, reason="terminal", allow_stale=True)
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
    daily_std = pstdev(daily_returns) if len(daily_returns) >= 2 else 0.0
    volatility = daily_std * math.sqrt(252.0) * 100.0 if daily_std > 0 else None
    sharpe_like = mean(daily_returns) / daily_std * math.sqrt(252.0) if daily_std > 0 else None

    return {
        "status": "simulated",
        "policy_version": E1_POLICY_VERSION,
        "mode": mode,
        "sessions": len(dates),
        "decision_dates": len(set(predictions_by_date) | (set(observed_symbols_by_date) if sampling_aware else set())),
        "max_positions": max_positions,
        "max_holding_sessions": max_holding_sessions,
        "round_trip_cost_percent": round_trip_cost_percent,
        "replacement_score_margin": replacement_score_margin,
        "execution_price_basis": "separate" if execution_price_history is not None else "mark_price_series",
        "terminal_liquidation": terminal_liquidation,
        "ending_wealth": previous_wealth,
        "total_return_percent": total_return,
        "approx_cagr_percent": cagr,
        "annualized_volatility_percent": volatility,
        "sharpe_like": sharpe_like,
        "max_drawdown_percent": _max_drawdown(curve),
        "total_transaction_cost_percent_of_initial_capital": total_cost * 100.0,
        "cumulative_turnover_x_initial_capital": total_notional,
        "annualized_turnover_x": total_notional / years,
        "buy_transactions": buys,
        "sell_transactions": sells,
        "replacements": replacements,
        "blocked_buy_attempts": blocked_buys,
        "blocked_sell_attempts": blocked_sells,
        "sampled_absence_holds": sampled_absence_holds,
        "terminal_stale_liquidations": terminal_stale_liquidations,
        "exit_reasons": {
            "expired": exits_expired,
            "ineligible": exits_ineligible,
            "reject": exits_reject,
        },
        "mean_positions": mean(active_position_counts) if active_position_counts else 0.0,
        "mean_completed_holding_sessions": mean(holding_sessions) if holding_sessions else None,
        "median_completed_holding_sessions": median(holding_sessions) if holding_sessions else None,
        "daily_returns": dated_returns,
        "equity_curve": [
            {"date": trading_date, "wealth": wealth}
            for trading_date, wealth in zip(dates, curve)
        ],
        "trade_log": trade_log,
    }