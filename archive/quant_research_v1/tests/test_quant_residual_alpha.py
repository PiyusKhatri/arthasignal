from __future__ import annotations

from datetime import date, timedelta

import pytest

from archive.quant_research_v1.services.quant_residual_alpha import (
    attach_residual_targets,
    build_baseline_candidate_pool,
    build_v4_predictions,
    compare_same_breadth,
    fit_affine_calibrator,
    fit_baseline_expectation,
    matched_baseline_selection,
    predict_baseline_expectation,
    select_v4_setups,
    selection_counts,
)


def _row(
    symbol: str,
    *,
    trading_date: date = date(2024, 1, 1),
    market_state: str = "strong_bull",
    sector_relative: float = 3.0,
    rs_market: float = 5.0,
    rs_sector: float = 3.0,
    excess: float = 3.0,
    adverse: float = -2.0,
    favorable: float = 6.0,
) -> dict:
    if market_state == "strong_bull":
        context = {
            "market_close": 120.0,
            "market_sma50": 110.0,
            "market_sma200": 100.0,
            "market_return_20d_percent": 4.0,
            "market_return_60d_percent": 12.0,
            "market_drawdown_252d_percent": -2.0,
            "market_annualized_volatility_60d_percent": 20.0,
            "sector_relative_strength_20d_percent": sector_relative,
        }
    elif market_state == "bear":
        context = {
            "market_close": 90.0,
            "market_sma50": 95.0,
            "market_sma200": 100.0,
            "market_return_20d_percent": -4.0,
            "market_return_60d_percent": -8.0,
            "market_drawdown_252d_percent": -12.0,
            "market_annualized_volatility_60d_percent": 25.0,
            "sector_relative_strength_20d_percent": sector_relative,
        }
    else:
        raise ValueError(market_state)

    return {
        "date": trading_date,
        "label_end_date": trading_date + timedelta(days=28),
        "symbol": symbol,
        "sector": "Bank",
        "stock_return_percent": 6.0,
        "market_return_percent": 2.0,
        "excess_return_percent": excess,
        "max_adverse_percent": adverse,
        "max_favorable_percent": favorable,
        "liquidity_bucket": "high",
        "horizon_slippage_sessions": 0,
        "regime_context": context,
        "features": {
            "return_5d": 2.0,
            "return_20d": 6.0,
            "return_60d": 12.0,
            "annualized_volatility_20d": 22.0,
            "drawdown_60d": -3.0,
            "turnover_ratio_20d": 1.4,
            "volume_ratio_20d": 1.3,
            "distance_sma50_percent": 5.0,
            "relative_strength_market_20d": rs_market,
            "relative_strength_sector_20d": rs_sector,
        },
    }


def test_candidate_pool_is_feature_only_and_ignores_future_outcomes() -> None:
    rows_a = [_row(f"S{i:02d}", rs_market=float(i), excess=float(i)) for i in range(20)]
    rows_b = [{**row, "excess_return_percent": -999.0 + index} for index, row in enumerate(rows_a)]

    pool_a = build_baseline_candidate_pool(rows_a)
    pool_b = build_baseline_candidate_pool(rows_b)

    assert [row["symbol"] for row in pool_a] == [row["symbol"] for row in pool_b]
    assert [row["baseline_rank"] for row in pool_a] == [row["baseline_rank"] for row in pool_b]
    assert len(pool_a) == 8


def test_bad_market_regime_is_not_a_v4_candidate_source() -> None:
    rows = [_row(f"B{i:02d}", market_state="bear", rs_market=float(i)) for i in range(20)]
    assert build_baseline_candidate_pool(rows) == []


def test_baseline_expectation_is_training_only() -> None:
    training = build_baseline_candidate_pool([
        _row(f"T{i:02d}", rs_market=float(i), excess=2.0 + (i % 3)) for i in range(20)
    ])
    model = fit_baseline_expectation(training, min_cell_rows=2)
    test_candidate = build_baseline_candidate_pool([
        _row(f"X{i:02d}", rs_market=float(i), excess=1000.0) for i in range(20)
    ])[0]

    before = predict_baseline_expectation(model, test_candidate)
    changed = {**test_candidate, "excess_return_percent": -1000.0}
    after = predict_baseline_expectation(model, changed)
    assert before == pytest.approx(after)


def test_residual_target_is_actual_minus_training_baseline_expectation() -> None:
    training = build_baseline_candidate_pool([
        _row(f"T{i:02d}", rs_market=float(i), excess=2.0) for i in range(20)
    ])
    model = fit_baseline_expectation(training, min_cell_rows=2)
    candidate = build_baseline_candidate_pool([
        _row(f"X{i:02d}", rs_market=float(i), excess=5.0) for i in range(20)
    ])[0]
    labeled = attach_residual_targets([candidate], model)[0]
    assert labeled["residual_alpha_percent"] == pytest.approx(
        labeled["excess_return_percent"] - labeled["baseline_expected_excess_percent"]
    )


def test_v4_can_override_top_momentum_name_but_matched_baseline_keeps_it() -> None:
    rows = build_baseline_candidate_pool([
        _row("TOP", rs_market=12.0, excess=-2.0),
        _row("SECOND", rs_market=10.0, excess=8.0),
        *[_row(f"FILL{i}", rs_market=float(8 - i), excess=0.0) for i in range(10)],
    ])
    baseline_model = fit_baseline_expectation(rows, min_cell_rows=1)
    rows = attach_residual_targets(rows, baseline_model)
    top = next(row for row in rows if row["symbol"] == "TOP")
    second = next(row for row in rows if row["symbol"] == "SECOND")

    predictions = build_v4_predictions(
        [top, second],
        [-1.0, 3.0],
        [0.40, 0.75],
        [2.0, 2.0],
        [5.0, 6.0],
    )
    selection = select_v4_setups(predictions)
    assert [candidate["row"]["symbol"] for candidate in selection["selected"]] == ["SECOND"]

    baseline = matched_baseline_selection(rows, selection_counts(selection))
    assert [candidate["row"]["symbol"] for candidate in baseline["selected"]] == ["TOP"]
    comparison = compare_same_breadth(selection, baseline)
    assert comparison["mean_incremental_net_excess_percent"] == pytest.approx(10.0)


def test_high_residual_but_bad_predicted_mae_is_rejected() -> None:
    rows = build_baseline_candidate_pool([
        _row(f"S{i:02d}", rs_market=float(20 - i), excess=5.0) for i in range(20)
    ])
    model = fit_baseline_expectation(rows, min_cell_rows=2)
    candidate = attach_residual_targets([rows[0]], model)[0]
    prediction = build_v4_predictions(
        [candidate],
        [5.0],
        [0.85],
        [12.0],
        [20.0],
    )
    assert select_v4_setups(prediction)["selected"] == []


def test_affine_calibrator_shrinks_prediction_scale_without_using_test_data() -> None:
    calibrator = fit_affine_calibrator([0.0, 2.0, 4.0, 6.0] * 10, [1.0, 2.0, 3.0, 4.0] * 10)
    assert calibrator is not None
    assert 0.0 < calibrator["slope"] < 1.0
