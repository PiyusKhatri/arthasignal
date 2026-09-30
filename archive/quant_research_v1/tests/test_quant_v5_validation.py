from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from archive.quant_research_v1.services.quant_v5_validation import (
    HistoricalGateMetrics,
    ReplayMetrics,
    build_historical_validation_report,
    derive_historical_gate_metrics,
    evaluate_historical_gate,
    moving_block_bootstrap,
    non_overlapping_cohort_bootstrap,
)


def _sessions(count: int) -> tuple[date, ...]:
    start = date(2024, 1, 1)
    return tuple(start + timedelta(days=index) for index in range(count))


def _row(
    signal_date: date,
    symbol: str,
    gross: float,
    *,
    fold: int = 1,
    mae: float = -1.0,
    probability: float | None = 0.75,
    recalibrated_probability: float | None = None,
    status: str = "resolved",
    void: bool = False,
) -> dict[str, object]:
    return {
        "date": signal_date,
        "symbol": symbol,
        "fold": fold,
        "gross_excess_return_percent": gross,
        "mae_percent": mae,
        "success_probability": probability,
        "recalibrated_success_probability": recalibrated_probability,
        "status": status,
        "void": void,
    }


def test_exact_breadth_excludes_mismatch_with_diagnostic_and_applies_fixed_cost() -> None:
    sessions = _sessions(2)
    v5 = [
        _row(sessions[0], "A", 4.0),
        _row(sessions[0], "B", 2.0),
        _row(sessions[1], "A", 5.0),
        _row(sessions[1], "B", 3.0),
    ]
    v41 = [
        _row(sessions[0], "A", 2.0, recalibrated_probability=0.65),
        _row(sessions[0], "C", 0.0, recalibrated_probability=0.45),
        _row(sessions[1], "A", 1.0, recalibrated_probability=0.55),
        _row(sessions[1], "B", 1.0, recalibrated_probability=0.55),
    ]
    baseline = [
        _row(sessions[0], "B", 1.0),
        _row(sessions[0], "C", -1.0),
        _row(sessions[1], "A", 2.0),
    ]

    report = build_historical_validation_report(
        v5_rows=v5,
        v41_rows=v41,
        baseline_rows=baseline,
        market_sessions=sessions,
        training_climatology_by_fold={1: 0.5},
        valid_purged_folds=(1,),
    )

    assert report.diagnostics.included_dates == (sessions[0],)
    assert report.diagnostics.excluded_dates == 1
    assert report.diagnostics.exclusions[0].reason == "breadth_mismatch"
    assert report.diagnostics.exclusions[0].counts == (2, 2, 1)
    assert report.strategy("v5").selected_rows == 2
    assert report.strategy("v5").mean_net_alpha_percent == pytest.approx(2.0)
    assert report.strategy("v5").median_net_alpha_percent == pytest.approx(2.0)
    assert report.paired("v41").date_mean_incremental_alpha_percent == pytest.approx(2.0)
    assert report.paired("baseline").date_mean_incremental_alpha_percent == pytest.approx(3.0)


def test_date_pairing_aggregates_stock_dependence_before_comparison() -> None:
    sessions = _sessions(2)
    v5 = [
        _row(sessions[0], "A", 5.0),
        _row(sessions[0], "B", 1.0),
        _row(sessions[1], "C", 3.0),
    ]
    v41 = [
        _row(sessions[0], "A", 1.0, recalibrated_probability=0.5),
        _row(sessions[0], "B", 1.0, recalibrated_probability=0.5),
        _row(sessions[1], "C", 2.0, recalibrated_probability=0.5),
    ]
    baseline = [
        _row(sessions[0], "A", 0.0),
        _row(sessions[0], "B", 0.0),
        _row(sessions[1], "C", 0.0),
    ]

    report = build_historical_validation_report(
        v5_rows=v5,
        v41_rows=v41,
        baseline_rows=baseline,
        market_sessions=sessions,
        training_climatology_by_fold={1: 0.5},
        valid_purged_folds=(1,),
    )

    comparisons = report.matched_dates
    assert [row.v5_minus_v41_percent for row in comparisons] == pytest.approx([2.0, 1.0])
    # The paired result gives each market date one observation, not each stock.
    assert report.paired("v41").date_mean_incremental_alpha_percent == pytest.approx(1.5)
    assert report.paired("v41").date_observations == 2


def test_bootstraps_are_deterministic_and_use_market_session_units() -> None:
    values = tuple((index % 7) / 10.0 if index % 5 else None for index in range(80))

    first = moving_block_bootstrap(values)
    second = moving_block_bootstrap(values)
    different_seed = moving_block_bootstrap(values, seed=20260822)
    cohorts = non_overlapping_cohort_bootstrap(values)

    assert first == second
    assert first.iterations == 1_000
    assert first.block_size == 20
    assert first.seed == 20260821
    assert first.units == 80
    assert first.observations == 64
    assert first != different_seed
    assert cohorts.cohort_means == pytest.approx((0.3, 0.2875, 0.275, 0.30625))
    assert cohorts.interval.units == 4


def test_void_rows_are_omitted_symmetrically_before_exact_breadth_matching() -> None:
    sessions = _sessions(1)
    v5 = [_row(sessions[0], "A", 3.0, void=True), _row(sessions[0], "B", 3.0)]
    v41 = [
        _row(sessions[0], "A", 1.0, recalibrated_probability=0.5),
        _row(sessions[0], "B", 1.0, recalibrated_probability=0.5),
    ]
    baseline = [_row(sessions[0], "A", 0.0), _row(sessions[0], "B", 0.0)]

    report = build_historical_validation_report(
        v5_rows=v5,
        v41_rows=v41,
        baseline_rows=baseline,
        market_sessions=sessions,
        training_climatology_by_fold={1: 0.5},
        valid_purged_folds=(1,),
    )

    assert report.diagnostics.included_dates == sessions
    assert report.matched_dates[0].breadth == 1
    assert report.diagnostics.omissions[0].reason == "symmetric_invalid_row_omission"
    assert report.diagnostics.omissions[0].counts == (1, 1, 1)


def test_risk_calibration_and_consecutive_active_date_jaccard_are_integrated() -> None:
    sessions = _sessions(3)
    v5 = [
        _row(sessions[0], "A", 3.0, mae=-6.0, probability=0.8),
        _row(sessions[0], "B", -1.0, mae=-2.0, probability=0.2),
        _row(sessions[1], "B", 2.0, mae=-1.0, probability=0.7),
        _row(sessions[1], "C", 2.0, mae=-3.0, probability=0.7),
        _row(sessions[2], "B", 2.0, mae=-1.0, probability=0.7),
        _row(sessions[2], "C", 2.0, mae=-3.0, probability=0.7),
    ]
    v41 = [
        _row(day, symbol, gross, mae=-7.0, recalibrated_probability=0.5)
        for day, symbol, gross in (
            (sessions[0], "A", 1.0),
            (sessions[0], "C", 1.0),
            (sessions[1], "A", 1.0),
            (sessions[1], "B", 1.0),
            (sessions[2], "A", 1.0),
            (sessions[2], "B", 1.0),
        )
    ]
    baseline = [
        _row(day, symbol, 0.0, probability=None)
        for day, symbol in (
            (sessions[0], "A"),
            (sessions[0], "B"),
            (sessions[1], "A"),
            (sessions[1], "C"),
            (sessions[2], "A"),
            (sessions[2], "C"),
        )
    ]

    report = build_historical_validation_report(
        v5_rows=v5,
        v41_rows=v41,
        baseline_rows=baseline,
        market_sessions=sessions,
        training_climatology_by_fold={1: 0.5},
        valid_purged_folds=(1,),
    )

    assert report.risk("v5").severe_drawdown_rate == pytest.approx(1 / 6)
    assert report.risk("v5").mean_adverse_mae_percent == pytest.approx(16 / 6)
    assert report.calibration_v5.brier_skill is not None
    assert report.calibration_v5.brier_skill > 0.0
    assert report.calibration_v5.expected_calibration_error == pytest.approx(4 / 15)
    assert report.consecutive_active_date_jaccards == pytest.approx((1 / 3, 1.0))
    assert report.median_consecutive_active_date_jaccard == pytest.approx(2 / 3)


def _passing_gate_metrics() -> HistoricalGateMetrics:
    return HistoricalGateMetrics(
        valid_purged_outer_folds=4,
        positive_v5_mean_folds=3,
        v5_beats_v41_folds=3,
        v5_minus_v41_mean_percent=0.5,
        v5_minus_v41_median_percent=0.4,
        date_bootstrap_v41_low_percent=0.1,
        cohort_bootstrap_v41_low_percent=0.05,
        v5_selected_median_net_alpha_percent=0.2,
        v5_severe_drawdown_rate=0.1,
        v41_severe_drawdown_rate=0.1,
        v5_mean_adverse_mae_percent=2.0,
        v41_mean_adverse_mae_percent=2.0,
        v5_brier_skill=0.1,
        v5_expected_calibration_error=0.05,
        v5_brier_no_worse_than_v41_folds=3,
        median_consecutive_active_date_jaccard=0.5,
        v5_replay_compounded_return_percent=1.0,
        v41_replay_compounded_return_percent=0.5,
        v5_replay_annualized_turnover=30.0,
        v5_replay_mean_holding_sessions=5.0,
        v5_replay_max_drawdown_percent=-10.0,
        v41_replay_max_drawdown_percent=-10.0,
        selected_v5_rows=100,
        active_dates=50,
        baseline_comparison_complete=True,
    )


@pytest.mark.parametrize(
    ("field", "bad_value", "failed_check"),
    (
        ("valid_purged_outer_folds", 3, "exactly_four_valid_purged_outer_folds"),
        ("positive_v5_mean_folds", 2, "positive_v5_mean_in_three_folds"),
        ("v5_beats_v41_folds", 2, "v5_beats_v41_in_three_folds"),
        ("v5_minus_v41_mean_percent", 0.0, "v5_minus_v41_mean_positive"),
        ("v5_minus_v41_median_percent", 0.0, "v5_minus_v41_median_positive"),
        ("date_bootstrap_v41_low_percent", 0.0, "date_bootstrap_lower_bound_positive"),
        ("cohort_bootstrap_v41_low_percent", 0.0, "cohort_bootstrap_lower_bound_positive"),
        ("v5_selected_median_net_alpha_percent", 0.0, "selected_v5_median_net_alpha_positive"),
        ("v5_severe_drawdown_rate", 0.11, "severe_drawdown_rate_no_worse_than_v41"),
        ("v5_mean_adverse_mae_percent", 2.1, "mean_mae_no_worse_than_v41"),
        ("v5_brier_skill", 0.0, "brier_skill_positive"),
        ("v5_expected_calibration_error", 0.051, "ece_at_most_005"),
        ("v5_brier_no_worse_than_v41_folds", 2, "brier_no_worse_than_v41_in_three_folds"),
        ("median_consecutive_active_date_jaccard", 0.49, "median_jaccard_at_least_050"),
        ("v5_replay_compounded_return_percent", 0.0, "v5_replay_compounded_return_positive"),
        ("v41_replay_compounded_return_percent", 1.1, "v5_replay_beats_v41"),
        ("v5_replay_annualized_turnover", 30.1, "v5_replay_turnover_at_most_30"),
        ("v5_replay_mean_holding_sessions", 4.9, "v5_replay_mean_holding_at_least_5"),
        ("v5_replay_max_drawdown_percent", -10.1, "v5_replay_drawdown_no_worse_than_v41"),
        ("selected_v5_rows", 99, "at_least_100_selected_v5_rows"),
        ("active_dates", 49, "at_least_50_active_dates"),
        ("baseline_comparison_complete", False, "baseline_comparison_complete"),
    ),
)
def test_every_historical_gate_is_fail_closed(
    field: str,
    bad_value: object,
    failed_check: str,
) -> None:
    passing = _passing_gate_metrics()
    assert evaluate_historical_gate(passing).status == "pass"

    evaluation = evaluate_historical_gate(replace(passing, **{field: bad_value}))

    assert evaluation.status == "research_rejected"
    assert evaluation.check(failed_check) is False


def test_missing_gate_metric_fails_closed_and_is_diagnostic() -> None:
    metrics = replace(_passing_gate_metrics(), v5_brier_skill=None)

    evaluation = evaluate_historical_gate(metrics)

    assert evaluation.status == "research_rejected"
    assert evaluation.check("brier_skill_positive") is False
    assert "v5_brier_skill" in evaluation.missing_metrics


def test_report_metrics_feed_gate_without_mutating_replay_input() -> None:
    sessions = _sessions(2)
    v5 = [_row(day, "A", 3.0, fold=index + 1) for index, day in enumerate(sessions)]
    v41 = [
        _row(day, "A", 1.0, fold=index + 1, recalibrated_probability=0.5)
        for index, day in enumerate(sessions)
    ]
    baseline = [_row(day, "A", 0.0, fold=index + 1) for index, day in enumerate(sessions)]
    report = build_historical_validation_report(
        v5_rows=v5,
        v41_rows=v41,
        baseline_rows=baseline,
        market_sessions=sessions,
        training_climatology_by_fold={1: 0.5, 2: 0.5},
        valid_purged_folds=(1, 2),
    )
    replay = ReplayMetrics(
        v5_compounded_return_percent=1.0,
        v41_compounded_return_percent=0.0,
        v5_annualized_turnover=1.0,
        v5_mean_holding_sessions=10.0,
        v5_max_drawdown_percent=-1.0,
        v41_max_drawdown_percent=-2.0,
    )

    metrics = derive_historical_gate_metrics(report, replay)

    assert metrics.valid_purged_outer_folds == 2
    assert metrics.v5_minus_v41_mean_percent == pytest.approx(2.0)
    assert replay.v5_compounded_return_percent == 1.0


def test_gate_rejects_valid_fold_ids_that_do_not_match_metric_folds() -> None:
    sessions = _sessions(4)
    v5 = [_row(day, "A", 3.0, fold=index + 1) for index, day in enumerate(sessions)]
    v41 = [
        _row(day, "A", 1.0, fold=index + 1, recalibrated_probability=0.5)
        for index, day in enumerate(sessions)
    ]
    baseline = [_row(day, "A", 0.0, fold=index + 1) for index, day in enumerate(sessions)]
    report = build_historical_validation_report(
        v5_rows=v5,
        v41_rows=v41,
        baseline_rows=baseline,
        market_sessions=sessions,
        training_climatology_by_fold={fold: 0.5 for fold in range(1, 6)},
        valid_purged_folds=(1, 2, 3, 5),
    )

    metrics = derive_historical_gate_metrics(report, ReplayMetrics())
    evaluation = evaluate_historical_gate(metrics)

    assert metrics.valid_purged_outer_folds is None
    assert "valid_purged_outer_folds" in evaluation.missing_metrics
    assert evaluation.check("exactly_four_valid_purged_outer_folds") is False


def test_baseline_companion_is_incomplete_when_replay_evidence_is_missing() -> None:
    sessions = _sessions(20)
    v5 = [_row(day, "A", 3.0) for day in sessions]
    v41 = [_row(day, "A", 1.0, recalibrated_probability=0.5) for day in sessions]
    baseline = [_row(day, "A", 0.0) for day in sessions]
    report = build_historical_validation_report(
        v5_rows=v5,
        v41_rows=v41,
        baseline_rows=baseline,
        market_sessions=sessions,
        training_climatology_by_fold={1: 0.5},
        valid_purged_folds=(1,),
    )

    missing = derive_historical_gate_metrics(report, ReplayMetrics())
    complete = derive_historical_gate_metrics(
        report,
        ReplayMetrics(
            baseline_compounded_return_percent=0.0,
            baseline_annualized_turnover=1.0,
            baseline_mean_holding_sessions=10.0,
            baseline_max_drawdown_percent=-2.0,
        ),
    )

    assert missing.baseline_comparison_complete is False
    assert complete.baseline_comparison_complete is True
