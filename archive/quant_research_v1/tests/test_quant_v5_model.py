from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import date, timedelta

import pytest

from archive.quant_research_v1.services.quant_residual_alpha import V4_META_FEATURE_NAMES, v4_feature_vector
from archive.quant_research_v1.services.quant_v5_model import (
    V5_CONFIG,
    V5_FEATURE_NAMES,
    V5_STABILITY_FEATURE_NAMES,
    CalibratedOutputs,
    ScoreObservation,
    brier_score,
    build_v5_feature_vector,
    calibration_confidence,
    calibration_metrics,
    composite_score,
    expected_calibration_error,
    score_v5_policy,
    shrink_current_score,
)


def _row(symbol: str = "AAA", trading_date: date = date(2026, 8, 21)) -> dict:
    return {
        "date": trading_date,
        "symbol": symbol,
        "features": {
            "return_5d": 2.0,
            "return_20d": 5.0,
            "return_60d": 12.0,
            "annualized_volatility_20d": 20.0,
            "drawdown_60d": -4.0,
            "turnover_ratio_20d": 1.4,
            "volume_ratio_20d": 1.3,
            "distance_sma50_percent": 4.0,
            "relative_strength_market_20d": 3.0,
            "relative_strength_sector_20d": 2.0,
        },
        "baseline_score": 1.5,
        "baseline_percentile": 0.9,
        "baseline_expected_excess_percent": 2.0,
        "v4_sector_regime": "leading",
        "liquidity_bucket": "high",
        "regime_context": {
            "market_return_20d_percent": 4.0,
            "market_return_60d_percent": 10.0,
            "market_drawdown_252d_percent": -3.0,
            "market_annualized_volatility_60d_percent": 21.0,
            "sector_relative_strength_20d_percent": 3.0,
        },
        "stability_features": {
            "trade_availability_20": 0.9,
            "turnover_cv_20": 0.5,
            "turnover_percentile": 0.8,
            "baseline_percentile_median_5": 0.75,
            "candidate_membership_count_5": 4,
        },
    }


def _outputs(
    success: float = 0.70,
    alpha: float = 0.60,
    safety: float = 0.90,
) -> CalibratedOutputs:
    return CalibratedOutputs(
        success_after_cost_probability=success,
        within_date_alpha_signal=alpha,
        severe_mae_safety_probability=safety,
    )


def test_v5_config_and_versions_are_frozen_with_fixed_weights() -> None:
    assert V5_CONFIG.model_version == "artha-pit-stability-v5-research-v1"
    assert V5_CONFIG.feature_version == "nepse-v5-pit-stability-v1"
    assert V5_CONFIG.policy_version == "2026-08-21-v5-research-v1"
    assert V5_CONFIG.composite_weights == pytest.approx((0.50, 0.30, 0.20))
    assert V5_CONFIG.score_shrinkage_weights == pytest.approx((0.70, 0.30))
    with pytest.raises(FrozenInstanceError):
        V5_CONFIG.model_version = "changed"  # type: ignore[misc]


def test_v5_feature_vector_is_v4_ex_ante_vector_plus_only_fixed_stability_fields() -> None:
    row = _row()
    vector = build_v5_feature_vector(row, stability_age_sessions=0)
    v4_vector = tuple(v4_feature_vector(row))

    assert V5_FEATURE_NAMES == tuple(V4_META_FEATURE_NAMES) + V5_STABILITY_FEATURE_NAMES
    assert vector[: len(v4_vector)] == pytest.approx(v4_vector)
    assert vector[len(v4_vector) :] == pytest.approx((0.9, 0.1, 0.8, 0.75, 0.8))
    assert len(vector) == len(V5_FEATURE_NAMES)


def test_future_outcomes_cannot_change_v5_feature_vector() -> None:
    row = _row()
    original = build_v5_feature_vector(row, stability_age_sessions=0)
    row.update(
        {
            "excess_return_percent": 999.0,
            "success_after_cost": True,
            "max_adverse_percent": -99.0,
            "max_favorable_percent": 999.0,
            "label_end_date": date(2030, 1, 1),
            "horizon_slippage_sessions": 999,
        }
    )
    assert build_v5_feature_vector(row, stability_age_sessions=0) == original


def test_dataset_shaped_nested_stability_features_are_the_only_stability_source() -> None:
    row = _row()
    row.update(
        {
            "trade_availability_20": 99,
            "turnover_cv_20": 99,
            "turnover_percentile": 99,
            "baseline_percentile_median_5": 99,
            "candidate_membership_count_5": 99,
        }
    )
    vector = build_v5_feature_vector(row, stability_age_sessions=0)
    assert vector[-5:] == pytest.approx((0.9, 0.1, 0.8, 0.75, 0.8))


def test_turnover_cv_is_clipped_to_five_before_fixed_scaling() -> None:
    row = _row()
    row["stability_features"]["turnover_cv_20"] = 50.0
    vector = build_v5_feature_vector(row, stability_age_sessions=0)
    assert vector[-4] == pytest.approx(1.0)


def test_policy_abstains_when_nested_stability_mapping_is_absent() -> None:
    row = _row()
    del row["stability_features"]
    decision = score_v5_policy([row], [_outputs()], stability_age_sessions=0)[0]
    assert decision.status == "abstained"
    assert decision.abstention_reason == "missing_stability_features"


@pytest.mark.parametrize(
    "field",
    V5_STABILITY_FEATURE_NAMES,
)
def test_policy_abstains_when_a_required_stability_input_is_missing(field: str) -> None:
    row = _row()
    del row["stability_features"][field]

    decision = score_v5_policy([row], [_outputs()], stability_age_sessions=0)[0]
    assert decision.status == "abstained"
    assert decision.abstention_reason == f"missing_stability_input:{field}"
    assert decision.shrunk_score is None
    assert decision.within_date_rank is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("trade_availability_20", 1.01),
        ("turnover_cv_20", -0.01),
        ("turnover_percentile", 1.01),
        ("baseline_percentile_median_5", -0.01),
        ("candidate_membership_count_5", 2.5),
    ],
)
def test_policy_abstains_on_out_of_contract_stability_ranges(
    field: str,
    value: float,
) -> None:
    row = _row()
    row["stability_features"][field] = value
    decision = score_v5_policy([row], [_outputs()], stability_age_sessions=0)[0]
    assert decision.status == "abstained"
    assert decision.abstention_reason == f"invalid_stability_input:{field}"


def test_policy_fails_closed_when_stability_inputs_are_stale() -> None:
    decision = score_v5_policy([_row()], [_outputs()], stability_age_sessions=2)[0]
    assert decision.status == "abstained"
    assert decision.abstention_reason == "stale_stability_inputs"
    assert decision.feature_vector is None


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"success_after_cost_probability": -0.01}, "success_after_cost_probability"),
        ({"success_after_cost_probability": 1.01}, "success_after_cost_probability"),
        ({"within_date_alpha_signal": float("nan")}, "within_date_alpha_signal"),
        ({"within_date_alpha_signal": 1.01}, "within_date_alpha_signal"),
        ({"severe_mae_safety_probability": -0.01}, "severe_mae_safety_probability"),
    ],
)
def test_calibrated_outputs_reject_invalid_probability_or_signal_ranges(
    kwargs: dict[str, float],
    message: str,
) -> None:
    values = {
        "success_after_cost_probability": 0.7,
        "within_date_alpha_signal": 0.6,
        "severe_mae_safety_probability": 0.9,
        **kwargs,
    }
    with pytest.raises(ValueError, match=message):
        CalibratedOutputs(**values)


def test_composite_score_uses_only_the_fixed_50_30_20_policy() -> None:
    assert composite_score(_outputs()) == pytest.approx(0.50 * 0.70 + 0.30 * 0.60 + 0.20 * 0.90)


def test_shrinkage_uses_only_the_five_most_recent_strictly_prior_sessions() -> None:
    current_date = date(2026, 1, 7)
    history = [
        ScoreObservation(
            date=current_date - timedelta(days=7 - index),
            symbol="AAA",
            composite_score=index / 10,
        )
        for index in range(1, 7)
    ]
    history.extend(
        [
            ScoreObservation(date=current_date, symbol="AAA", composite_score=0.99),
            ScoreObservation(date=current_date + timedelta(days=1), symbol="AAA", composite_score=0.0),
            ScoreObservation(date=current_date - timedelta(days=1), symbol="OTHER", composite_score=0.0),
        ]
    )

    result = shrink_current_score(
        0.8,
        trading_date=current_date,
        symbol="AAA",
        prior_scores=history,
        prior_market_dates=[current_date - timedelta(days=index) for index in range(5, 0, -1)],
    )
    assert result.prior_session_scores == pytest.approx((0.2, 0.3, 0.4, 0.5, 0.6))
    assert result.shrunk_score == pytest.approx(0.70 * 0.8 + 0.30 * 0.4)


def test_shrinkage_without_prior_history_preserves_the_current_score() -> None:
    result = shrink_current_score(
        0.8,
        trading_date=date(2026, 1, 7),
        symbol="AAA",
        prior_scores=(),
    )
    assert result.prior_session_scores == ()
    assert result.shrunk_score == pytest.approx(0.8)


def test_shrinkage_uses_the_prior_score_median_not_mean() -> None:
    current_date = date(2026, 1, 7)
    history = [
        ScoreObservation(
            date=current_date - timedelta(days=5 - index),
            symbol="AAA",
            composite_score=score,
        )
        for index, score in enumerate((0.1, 0.1, 0.1, 0.1, 0.9))
    ]
    result = shrink_current_score(
        0.8,
        trading_date=current_date,
        symbol="AAA",
        prior_scores=history,
        prior_market_dates=[current_date - timedelta(days=index) for index in range(5, 0, -1)],
    )
    assert result.shrunk_score == pytest.approx(0.70 * 0.8 + 0.30 * 0.1)


def test_within_date_ranking_is_deterministic_and_symbol_breaks_score_ties() -> None:
    rows = [_row("CCC"), _row("AAA"), _row("BBB")]
    outputs = [_outputs(0.5, 0.5, 0.5), _outputs(0.8, 0.8, 0.8), _outputs(0.5, 0.5, 0.5)]
    forward = score_v5_policy(rows, outputs, stability_age_sessions=0)
    reverse = score_v5_policy(list(reversed(rows)), list(reversed(outputs)), stability_age_sessions=0)

    assert [(item.symbol, item.within_date_rank) for item in forward] == [
        ("AAA", 1),
        ("BBB", 2),
        ("CCC", 3),
    ]
    assert forward == reverse


def test_score_ties_break_by_current_composite_then_baseline_percentile_then_symbol() -> None:
    trading_date = date(2026, 8, 21)
    higher_current = _row("ZZZ", trading_date)
    higher_baseline = _row("CCC", trading_date)
    lower_baseline = _row("AAA", trading_date)
    higher_baseline["baseline_percentile"] = 0.9
    lower_baseline["baseline_percentile"] = 0.8
    histories = [
        ScoreObservation(date=date(2026, 8, 20), symbol="ZZZ", composite_score=0.1),
        ScoreObservation(date=date(2026, 8, 20), symbol="CCC", composite_score=0.8),
        ScoreObservation(date=date(2026, 8, 20), symbol="AAA", composite_score=0.8),
    ]
    decisions = score_v5_policy(
        [lower_baseline, higher_current, higher_baseline],
        [_outputs(0.5, 0.5, 0.5), _outputs(0.8, 0.8, 0.8), _outputs(0.5, 0.5, 0.5)],
        stability_age_sessions=0,
        prior_scores=histories,
        prior_market_dates=[date(2026, 8, 20)],
    )
    assert [decision.symbol for decision in decisions] == ["ZZZ", "CCC", "AAA"]


def test_scoring_does_not_mutate_rows_outputs_or_score_history() -> None:
    rows = [_row("AAA")]
    outputs = [_outputs()]
    history = [ScoreObservation(date=date(2026, 8, 20), symbol="AAA", composite_score=0.4)]
    original_rows = deepcopy(rows)
    original_outputs = deepcopy(outputs)
    original_history = deepcopy(history)

    score_v5_policy(
        rows,
        outputs,
        stability_age_sessions=0,
        prior_scores=history,
        prior_market_dates=[date(2026, 8, 20)],
    )

    assert rows == original_rows
    assert outputs == original_outputs
    assert history == original_history


def test_score_history_outside_prior_five_market_sessions_is_ignored() -> None:
    trading_date = date(2026, 8, 21)
    stale = ScoreObservation(
        date=date(2026, 8, 1),
        symbol="AAA",
        composite_score=0.0,
    )

    decision = score_v5_policy(
        [_row("AAA", trading_date)],
        [_outputs(0.8, 0.8, 0.8)],
        stability_age_sessions=0,
        prior_scores=[stale],
        prior_market_dates=[date(2026, 8, day) for day in range(16, 21)],
    )[0]

    assert decision.prior_session_scores == ()
    assert decision.shrunk_score == pytest.approx(decision.current_composite_score)


def test_duplicate_symbol_date_candidates_are_rejected_as_ambiguous() -> None:
    with pytest.raises(ValueError, match="duplicate candidate"):
        score_v5_policy(
            [_row("AAA"), _row("AAA")],
            [_outputs(), _outputs()],
            stability_age_sessions=0,
        )


def test_brier_ece_and_calibration_metrics_are_deterministic() -> None:
    probabilities = [0.0, 0.25, 0.75, 1.0]
    outcomes = [0, 0, 1, 1]
    assert brier_score(probabilities, outcomes) == pytest.approx(0.03125)
    assert expected_calibration_error(probabilities, outcomes, bins=2) == pytest.approx(0.125)

    metrics = calibration_metrics(probabilities, outcomes, bins=2)
    assert metrics.samples == 4
    assert metrics.brier_score == pytest.approx(0.03125)
    assert metrics.expected_calibration_error == pytest.approx(0.125)
    assert calibration_confidence(metrics, target_samples=4) == pytest.approx(0.8203125)


@pytest.mark.parametrize(
    ("probabilities", "outcomes", "message"),
    [
        ([], [], "non-empty"),
        ([0.5], [], "equal length"),
        ([1.1], [1], "probabilities"),
        ([0.5], [2], "outcomes"),
    ],
)
def test_calibration_helpers_validate_inputs(
    probabilities: list[float],
    outcomes: list[int],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        brier_score(probabilities, outcomes)

    if probabilities and len(probabilities) == len(outcomes):
        with pytest.raises(ValueError, match=message):
            expected_calibration_error(probabilities, outcomes)


def test_ece_rejects_invalid_bin_count() -> None:
    with pytest.raises(ValueError, match="bins"):
        expected_calibration_error([0.5], [1], bins=0)
