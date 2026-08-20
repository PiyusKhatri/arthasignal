from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.services.quant_execution_policy_e1 import (
    E1_REPLACEMENT_SCORE_MARGIN,
    group_predictions_by_date,
    simulate_execution_policy_e1,
)
from src.services.quant_portfolio_simulator import simulate_rebalance_portfolio
from src.services.quant_v41_heartbeat import heartbeat_fingerprint


def _candidate(
    symbol: str,
    trading_date: date,
    *,
    final_score: float,
    baseline_percentile: float,
    action: str = "hold",
    expected_excess: float = 2.0,
) -> dict:
    return {
        "row": {
            "symbol": symbol,
            "date": trading_date,
            "baseline_percentile": baseline_percentile,
            "baseline_score": baseline_percentile,
            "v4_market_regime": "bull",
        },
        "final_score": final_score,
        "override_action": action,
        "expected_excess_return_percent": expected_excess,
        "market_regime": "bull",
    }


def _dates(count: int) -> list[date]:
    start = date(2026, 1, 1)
    return [start + timedelta(days=index) for index in range(count)]


def _prices(symbols: list[str], dates: list[date]) -> dict[str, dict[date, float]]:
    return {
        symbol: {trading_date: 100.0 + index for index, trading_date in enumerate(dates)}
        for symbol in symbols
    }


def test_e1_does_not_replace_on_small_rank_drift() -> None:
    dates = _dates(4)
    predictions = [
        _candidate("AAA", dates[0], final_score=0.90, baseline_percentile=0.90),
        _candidate("BBB", dates[0], final_score=0.80, baseline_percentile=0.80),
        _candidate("AAA", dates[1], final_score=0.70, baseline_percentile=0.70),
        _candidate("BBB", dates[1], final_score=0.78, baseline_percentile=0.78),
    ]
    result = simulate_execution_policy_e1(
        market_dates=dates,
        predictions_by_date=group_predictions_by_date(predictions),
        price_history=_prices(["AAA", "BBB"], dates),
        mode="v41",
        max_positions=1,
        max_holding_sessions=20,
        round_trip_cost_percent=1.0,
    )
    assert result["replacements"] == 0
    buys = [trade for trade in result["trade_log"] if trade["side"] == "buy"]
    assert [trade["symbol"] for trade in buys] == ["AAA"]


def test_e1_replaces_only_after_fixed_margin_is_crossed() -> None:
    dates = _dates(4)
    predictions = [
        _candidate("AAA", dates[0], final_score=0.90, baseline_percentile=0.90),
        _candidate("BBB", dates[0], final_score=0.80, baseline_percentile=0.80),
        _candidate("AAA", dates[1], final_score=0.55, baseline_percentile=0.55),
        _candidate(
            "BBB",
            dates[1],
            final_score=0.55 + E1_REPLACEMENT_SCORE_MARGIN + 0.01,
            baseline_percentile=0.75,
        ),
    ]
    result = simulate_execution_policy_e1(
        market_dates=dates,
        predictions_by_date=group_predictions_by_date(predictions),
        price_history=_prices(["AAA", "BBB"], dates),
        mode="v41",
        max_positions=1,
        max_holding_sessions=20,
        round_trip_cost_percent=1.0,
    )
    assert result["replacements"] == 1
    replacement_buys = [
        trade for trade in result["trade_log"]
        if trade["side"] == "buy" and trade["reason"] == "replacement"
    ]
    assert [trade["symbol"] for trade in replacement_buys] == ["BBB"]


def test_e1_reject_forces_exit() -> None:
    dates = _dates(4)
    predictions = [
        _candidate("AAA", dates[0], final_score=0.90, baseline_percentile=0.90),
        _candidate("AAA", dates[1], final_score=0.75, baseline_percentile=0.75, action="reject"),
    ]
    result = simulate_execution_policy_e1(
        market_dates=dates,
        predictions_by_date=group_predictions_by_date(predictions),
        price_history=_prices(["AAA"], dates),
        mode="v41",
        max_positions=1,
        max_holding_sessions=20,
    )
    assert result["exit_reasons"]["reject"] == 1


def test_e1_holding_cap_applies_without_new_decision() -> None:
    dates = _dates(6)
    predictions = [
        _candidate("AAA", dates[0], final_score=0.90, baseline_percentile=0.90),
    ]
    result = simulate_execution_policy_e1(
        market_dates=dates,
        predictions_by_date=group_predictions_by_date(predictions),
        price_history=_prices(["AAA"], dates),
        mode="v41",
        max_positions=1,
        max_holding_sessions=2,
    )
    assert result["exit_reasons"]["expired"] == 1
    assert result["mean_completed_holding_sessions"] == pytest.approx(2.0)


def test_e1_reduces_turnover_when_top_rank_oscillates_inside_margin() -> None:
    dates = _dates(8)
    predictions: list[dict] = []
    reactive_signals: dict[date, list[str]] = {}
    for index, trading_date in enumerate(dates[:6]):
        if index % 2 == 0:
            aaa, bbb = 0.80, 0.75
            reactive_signals[trading_date] = ["AAA"]
        else:
            aaa, bbb = 0.75, 0.80
            reactive_signals[trading_date] = ["BBB"]
        predictions.extend(
            [
                _candidate("AAA", trading_date, final_score=aaa, baseline_percentile=aaa),
                _candidate("BBB", trading_date, final_score=bbb, baseline_percentile=bbb),
            ]
        )

    price_history = _prices(["AAA", "BBB"], dates)
    sticky = simulate_execution_policy_e1(
        market_dates=dates,
        predictions_by_date=group_predictions_by_date(predictions),
        price_history=price_history,
        mode="v41",
        max_positions=1,
        max_holding_sessions=20,
    )
    reactive = simulate_rebalance_portfolio(
        market_dates=dates,
        signals_by_date=reactive_signals,
        price_history=price_history,
        max_positions=1,
        max_holding_sessions=20,
        round_trip_cost_percent=1.0,
    )
    assert sticky["annualized_turnover_x"] < reactive["annualized_turnover_x"]
    assert sticky["total_transaction_cost_percent_of_initial_capital"] < reactive["total_transaction_cost_percent_of_initial_capital"]


def test_heartbeat_fingerprint_is_deterministic_and_content_sensitive() -> None:
    payload = {
        "model_snapshot_id": 7,
        "as_of_date": "2026-08-21",
        "run_status": "abstained",
        "eligible_rows": 178,
        "candidate_rows": 0,
    }
    assert heartbeat_fingerprint(payload) == heartbeat_fingerprint(dict(payload))
    assert heartbeat_fingerprint(payload) != heartbeat_fingerprint({**payload, "candidate_rows": 1})
