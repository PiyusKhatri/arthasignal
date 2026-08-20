from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.pipeline.v41_shadow import _fingerprint, _target_date
from src.services.quant_portfolio_simulator import (
    block_bootstrap_incremental_returns,
    simulate_rebalance_portfolio,
)
from src.services.quant_v41_artifact import (
    _decode_baseline,
    _encode_baseline,
    _train_calibration_split,
)


def test_baseline_expectation_round_trips_without_losing_tuple_keys() -> None:
    model = {
        "detailed": {("bull", "leading", "top_5"): 2.5},
        "market_bucket": {("bull", "top_5"): 1.5},
        "bucket_only": {"top_5": 1.0},
        "overall": 0.5,
        "training_candidates": 123,
        "min_cell_rows": 20,
    }
    decoded = _decode_baseline(_encode_baseline(model))
    assert decoded["detailed"][("bull", "leading", "top_5")] == pytest.approx(2.5)
    assert decoded["market_bucket"][("bull", "top_5")] == pytest.approx(1.5)
    assert decoded["bucket_only"]["top_5"] == pytest.approx(1.0)


def test_frozen_train_calibration_split_purges_forward_labels() -> None:
    start = date(2020, 1, 1)
    rows = []
    for index in range(140):
        trading_date = start + timedelta(days=index)
        rows.append(
            {
                "date": trading_date,
                "label_end_date": trading_date + timedelta(days=20),
            }
        )
    train, calibration, calibration_start = _train_calibration_split(rows)
    assert calibration_start is not None
    assert train and calibration
    assert max(row["label_end_date"] for row in train) < calibration_start
    assert min(row["date"] for row in calibration) >= calibration_start


def test_shadow_prediction_fingerprint_is_stable_and_content_sensitive() -> None:
    payload = {"symbol": "NABIL", "date": "2026-08-21", "score": 0.72}
    first = _fingerprint(payload)
    second = _fingerprint(dict(payload))
    changed = _fingerprint({**payload, "score": 0.73})
    assert first == second
    assert first != changed
    assert len(first) == 64


def test_shadow_target_uses_market_sessions_not_calendar_days() -> None:
    sessions = [date(2026, 1, 1) + timedelta(days=index) for index in range(30)]
    assert _target_date(sessions[0], 20, sessions) == sessions[20]


def test_portfolio_holding_cap_closes_without_new_signal() -> None:
    dates = [date(2026, 1, 1) + timedelta(days=index) for index in range(6)]
    prices = {"AAA": {trading_date: 100.0 + index for index, trading_date in enumerate(dates)}}
    result = simulate_rebalance_portfolio(
        market_dates=dates,
        signals_by_date={dates[0]: ["AAA"]},
        price_history=prices,
        max_positions=1,
        max_holding_sessions=2,
        round_trip_cost_percent=1.0,
    )
    assert result["sell_transactions"] >= 1
    assert result["mean_completed_holding_sessions"] == pytest.approx(2.0)
    assert result["total_transaction_cost_percent_of_initial_capital"] > 0.0


def test_equal_price_round_trip_loses_only_transaction_costs() -> None:
    dates = [date(2026, 1, 1) + timedelta(days=index) for index in range(4)]
    prices = {"AAA": {trading_date: 100.0 for trading_date in dates}}
    result = simulate_rebalance_portfolio(
        market_dates=dates,
        signals_by_date={dates[0]: ["AAA"]},
        price_history=prices,
        max_positions=1,
        max_holding_sessions=20,
        round_trip_cost_percent=1.0,
    )
    assert result["total_return_percent"] < 0.0
    assert result["total_transaction_cost_percent_of_initial_capital"] > 0.9


def test_block_bootstrap_detects_persistent_incremental_edge() -> None:
    dates = [date(2026, 1, 1) + timedelta(days=index) for index in range(80)]
    better = [{"date": trading_date, "return": 0.002} for trading_date in dates]
    baseline = [{"date": trading_date, "return": 0.001} for trading_date in dates]
    result = block_bootstrap_incremental_returns(better, baseline, block_length=10, iterations=200)
    assert result["annualized_incremental_return_95"]["low"] > 0.0
