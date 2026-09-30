from __future__ import annotations

from datetime import date, timedelta

import pytest

from archive.quant_research_v1.services.quant_residual_alpha import (
    attach_residual_targets,
    build_baseline_candidate_pool,
    fit_baseline_expectation,
    matched_baseline_selection,
    selection_counts,
)
from archive.quant_research_v1.services.quant_residual_stability import (
    V41_MAX_OVERRIDE,
    apply_monotonic_residual_calibrator,
    build_v41_predictions,
    fit_monotonic_residual_calibrator,
    select_v41_setups,
)


def _row(
    symbol: str,
    *,
    trading_date: date = date(2024, 1, 1),
    rs_market: float = 5.0,
    rs_sector: float = 3.0,
    excess: float = 3.0,
    adverse: float = -2.0,
    favorable: float = 6.0,
) -> dict:
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
        "regime_context": {
            "market_close": 120.0,
            "market_sma50": 110.0,
            "market_sma200": 100.0,
            "market_return_20d_percent": 4.0,
            "market_return_60d_percent": 12.0,
            "market_drawdown_252d_percent": -2.0,
            "market_annualized_volatility_60d_percent": 20.0,
            "sector_relative_strength_20d_percent": 3.0,
        },
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


def _labeled_candidates(count: int = 20) -> list[dict]:
    candidates = build_baseline_candidate_pool(
        [
            _row(
                f"S{index:02d}",
                rs_market=float(count - index),
                excess=2.0 + (index % 4),
            )
            for index in range(count)
        ]
    )
    model = fit_baseline_expectation(candidates, min_cell_rows=1)
    return attach_residual_targets(candidates, model)


def test_monotonic_residual_calibrator_enforces_non_decreasing_levels() -> None:
    raw = [float(index) for index in range(100)]
    # Intentionally non-monotonic realized bins: strong, weak, negative, strong, very strong.
    actual = []
    for index in range(100):
        bucket = index // 20
        actual.append([1.0, 3.0, -2.0, 2.0, 5.0][bucket])

    calibrator = fit_monotonic_residual_calibrator(raw, actual, bins=5)
    assert calibrator is not None
    levels = calibrator["levels"]
    assert levels == sorted(levels)
    assert calibrator["samples"] == 100


def test_monotonic_calibration_is_determined_before_test_values_are_seen() -> None:
    raw = [float(index) / 10.0 for index in range(100)]
    actual = [value * 0.4 - 1.0 for value in raw]
    calibrator = fit_monotonic_residual_calibrator(raw, actual, bins=5)
    assert calibrator is not None

    before = apply_monotonic_residual_calibrator(3.5, calibrator)
    # Applying the already-fitted map to an extreme test score cannot mutate it.
    _ = apply_monotonic_residual_calibrator(999.0, calibrator)
    after = apply_monotonic_residual_calibrator(3.5, calibrator)
    assert before == pytest.approx(after)


def test_v41_ml_override_is_bounded_around_baseline_percentile() -> None:
    rows = _labeled_candidates()
    predictions = build_v41_predictions(
        rows,
        [10.0] * len(rows),
        [0.99] * len(rows),
        [1.0] * len(rows),
        [20.0] * len(rows),
    )
    assert predictions
    for prediction in predictions:
        assert abs(prediction["override_correction"]) <= V41_MAX_OVERRIDE + 1e-12
        assert prediction["final_score"] == pytest.approx(
            prediction["baseline_percentile"] + prediction["override_correction"]
        )


def test_ambiguous_ml_evidence_preserves_strong_baseline_name() -> None:
    rows = _labeled_candidates()
    top = rows[0]
    second = rows[1]
    assert top["baseline_rank"] < second["baseline_rank"]

    predictions = build_v41_predictions(
        [top, second],
        [0.2, 0.3],
        [0.52, 0.53],
        [5.0, 5.0],
        [7.0, 7.0],
    )
    selection = select_v41_setups(predictions)
    symbols = [candidate["row"]["symbol"] for candidate in selection["selected"]]
    assert symbols[0] == top["symbol"]


def test_strong_residual_evidence_can_override_baseline_ordering() -> None:
    rows = _labeled_candidates()
    top = rows[0]
    weaker_baseline = rows[min(4, len(rows) - 1)]

    predictions = build_v41_predictions(
        [top, weaker_baseline],
        [0.1, 4.0],
        [0.50, 0.80],
        [6.0, 2.0],
        [7.0, 8.0],
    )
    selection = select_v41_setups(predictions)
    symbols = [candidate["row"]["symbol"] for candidate in selection["selected"]]
    assert symbols[0] == weaker_baseline["symbol"]
    assert next(candidate for candidate in predictions if candidate["row"]["symbol"] == weaker_baseline["symbol"])["override_action"] == "promote"


def test_extreme_predicted_mae_rejects_candidate_even_with_high_alpha() -> None:
    row = _labeled_candidates()[0]
    prediction = build_v41_predictions(
        [row],
        [6.0],
        [0.90],
        [13.0],
        [25.0],
    )
    assert prediction[0]["override_action"] == "reject"
    assert select_v41_setups(prediction)["selected"] == []


def test_matched_baseline_receives_exact_same_date_and_breadth() -> None:
    rows = _labeled_candidates()
    predictions = build_v41_predictions(
        rows,
        [1.5] * len(rows),
        [0.62] * len(rows),
        [4.0] * len(rows),
        [7.0] * len(rows),
    )
    selection = select_v41_setups(predictions)
    baseline = matched_baseline_selection(rows, selection_counts(selection))

    selected_counts = selection_counts(selection)
    baseline_counts = selection_counts(baseline)
    assert selected_counts == baseline_counts
