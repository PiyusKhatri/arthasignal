from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, timedelta
from typing import Any

import pytest

from src.services import quant_v5_training
from src.services.quant_v5_model import V5_CONFIG, CalibratedOutputs
from src.services.quant_v5_training import (
    V5_TRAINING_CONFIG,
    PlattCalibrator,
    V5ModelBundle,
    V5TrainingError,
    build_v5_feature_matrix,
    fit_platt_calibrator,
    fit_v5_model_bundle,
    predict_v5_rows,
    within_date_average_rank_percentiles,
)


def _row(
    trading_date: date,
    symbol: str,
    *,
    marker: float = 0.0,
    net_alpha: float = 2.0,
    severe_mae: bool = False,
) -> dict[str, Any]:
    return {
        "date": trading_date,
        "symbol": symbol,
        "features": {
            "return_5d": marker,
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
        "net_alpha_percent": net_alpha,
        "excess_return_percent": net_alpha + 1.0,
        "success_after_cost": net_alpha > 0.0,
        "severe_mae": severe_mae,
    }


def _training_rows() -> list[dict[str, Any]]:
    start = date(2020, 1, 1)
    return [
        _row(
            start + timedelta(days=day),
            f"S{stock:02d}",
            marker=float(day * 20 + stock),
            net_alpha=2.0 if stock % 2 == 0 else -2.0,
            severe_mae=stock % 2 == 0,
        )
        for day in range(25)
        for stock in range(20)
    ]


def _calibration_rows() -> list[dict[str, Any]]:
    start = date(2021, 1, 1)
    return [
        _row(
            start + timedelta(days=day),
            f"C{stock:02d}",
            marker=10_000.0 + day * 10 + stock,
            net_alpha=3.0 if stock % 2 == 0 else -3.0,
            severe_mae=stock % 2 != 0,
        )
        for day in range(6)
        for stock in range(10)
    ]


def _bundle() -> V5ModelBundle:
    return V5ModelBundle(
        ranker=object(),
        success_classifier=object(),
        severe_mae_classifier=object(),
        success_calibrator=PlattCalibrator(intercept=0.0, slope=1.0, samples=60),
        severe_mae_calibrator=PlattCalibrator(intercept=0.0, slope=1.0, samples=60),
        training_rows=500,
        ranker_training_rows=500,
        ranker_training_dates=25,
        calibration_rows=60,
    )


def test_frozen_training_parameters_match_protocol_exactly() -> None:
    assert V5_TRAINING_CONFIG.ranker_rounds == 180
    assert dict(V5_TRAINING_CONFIG.ranker_parameters) == {
        "objective": "rank:ndcg",
        "eval_metric": "ndcg@10",
        "max_depth": 4,
        "eta": 0.035,
        "min_child_weight": 10,
        "subsample": 0.80,
        "colsample_bytree": 0.80,
        "lambda": 3.0,
        "alpha": 0.30,
        "tree_method": "hist",
        "seed": 20260821,
        "nthread": 1,
    }
    assert V5_TRAINING_CONFIG.classifier_rounds == 220
    assert dict(V5_TRAINING_CONFIG.classifier_parameters) == {
        "objective": "binary:logistic",
        "eval_metric": "logloss",
        "max_depth": 4,
        "eta": 0.035,
        "min_child_weight": 8,
        "subsample": 0.78,
        "colsample_bytree": 0.78,
        "lambda": 2.5,
        "alpha": 0.25,
        "max_delta_step": 1,
        "tree_method": "hist",
        "seed": 20260821,
        "nthread": 1,
    }
    assert V5_TRAINING_CONFIG.platt_iterations == 500
    assert V5_TRAINING_CONFIG.platt_learning_rate == pytest.approx(0.035)
    assert V5_TRAINING_CONFIG.platt_l2 == pytest.approx(0.02)
    with pytest.raises(FrozenInstanceError):
        V5_TRAINING_CONFIG.seed = 1  # type: ignore[misc]


def test_feature_matrix_has_one_fixed_width_vector_per_row() -> None:
    rows = [_row(date(2026, 1, 1), "AAA"), _row(date(2026, 1, 1), "BBB")]
    matrix = build_v5_feature_matrix(rows, stability_age_sessions=0)

    assert isinstance(matrix, tuple)
    assert len(matrix) == 2
    assert all(isinstance(vector, tuple) for vector in matrix)
    assert {len(vector) for vector in matrix} == {len(V5_CONFIG.feature_names)}


def test_same_date_average_rank_percentiles_are_tie_aware_and_order_stable() -> None:
    rows = [
        {"date": date(2026, 1, 2), "symbol": "D"},
        {"date": date(2026, 1, 1), "symbol": "B"},
        {"date": date(2026, 1, 1), "symbol": "A"},
        {"date": date(2026, 1, 1), "symbol": "C"},
        {"date": date(2026, 1, 2), "symbol": "E"},
    ]
    scores = [7.0, 2.0, 1.0, 2.0, 9.0]

    assert within_date_average_rank_percentiles(rows, scores) == pytest.approx(
        (0.0, 0.75, 0.0, 0.75, 1.0)
    )


def test_training_fits_heads_only_on_train_and_platt_maps_only_on_calibration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    train_rows = _training_rows()
    calibration_rows = _calibration_rows()
    calls: dict[str, Any] = {"classifiers": [], "platt": []}

    def fake_fit_ranker(matrix, targets, group_sizes):
        calls["ranker"] = (matrix, targets, group_sizes)
        return "ranker"

    def fake_fit_classifier(matrix, labels, *, head):
        calls["classifiers"].append((head, matrix, labels))
        return head

    def fake_predict_probabilities(model, matrix):
        if model == "success":
            return tuple(0.8 if index % 2 == 0 else 0.2 for index in range(len(matrix)))
        return tuple(0.2 if index % 2 == 0 else 0.8 for index in range(len(matrix)))

    def fake_fit_platt(probabilities, outcomes):
        calls["platt"].append((probabilities, outcomes))
        return PlattCalibrator(intercept=0.0, slope=1.0, samples=len(outcomes))

    monkeypatch.setattr(quant_v5_training, "_fit_ranker", fake_fit_ranker)
    monkeypatch.setattr(quant_v5_training, "_fit_classifier", fake_fit_classifier)
    monkeypatch.setattr(
        quant_v5_training,
        "_predict_probabilities",
        fake_predict_probabilities,
    )
    monkeypatch.setattr(quant_v5_training, "fit_platt_calibrator", fake_fit_platt)

    bundle = fit_v5_model_bundle(train_rows, calibration_rows, stability_age_sessions=0)

    rank_matrix, rank_targets, groups = calls["ranker"]
    assert len(rank_matrix) == 500
    assert groups == (20,) * 25
    assert set(rank_targets) == {-2.0, 2.0}
    assert max(vector[0] for vector in rank_matrix) < 10_000.0
    assert [head for head, _, _ in calls["classifiers"]] == ["success", "severe_mae"]
    assert all(len(matrix) == 500 for _, matrix, _ in calls["classifiers"])
    assert all(max(vector[0] for vector in matrix) < 10_000.0 for _, matrix, _ in calls["classifiers"])
    assert len(calls["platt"]) == 2
    assert calls["platt"][0][1] == tuple(index % 2 == 0 for index in range(60))
    assert calls["platt"][1][1] == tuple(index % 2 != 0 for index in range(60))
    assert bundle.training_rows == 500
    assert bundle.calibration_rows == 60


def test_training_uses_excess_return_minus_frozen_cost_when_net_alpha_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    train_rows = _training_rows()
    calibration_rows = _calibration_rows()
    for row in train_rows + calibration_rows:
        del row["net_alpha_percent"]
    captured: dict[str, tuple[float, ...]] = {}

    def fake_fit_ranker(matrix, targets, group_sizes):
        captured["targets"] = targets
        return "ranker"

    monkeypatch.setattr(quant_v5_training, "_fit_ranker", fake_fit_ranker)
    monkeypatch.setattr(
        quant_v5_training,
        "_fit_classifier",
        lambda matrix, labels, *, head: head,
    )
    monkeypatch.setattr(
        quant_v5_training,
        "_predict_probabilities",
        lambda model, matrix: tuple(0.8 if index % 2 == 0 else 0.2 for index in range(len(matrix))),
    )
    monkeypatch.setattr(
        quant_v5_training,
        "fit_platt_calibrator",
        lambda probabilities, outcomes: PlattCalibrator(0.0, 1.0, len(outcomes)),
    )

    fit_v5_model_bundle(train_rows, calibration_rows, stability_age_sessions=0)
    assert set(captured["targets"]) == {-2.0, 2.0}


class _OutcomeGuard(dict[str, Any]):
    forbidden = {
        "net_alpha_percent",
        "excess_return_percent",
        "success_after_cost",
        "success",
        "severe_mae",
        "mae_magnitude_percent",
        "max_adverse_percent",
        "label_end_date",
    }

    def __getitem__(self, key: str) -> Any:
        if key in self.forbidden:
            raise AssertionError(f"inference read forbidden outcome: {key}")
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:
        if key in self.forbidden:
            raise AssertionError(f"inference read forbidden outcome: {key}")
        return super().get(key, default)


def test_prediction_never_reads_test_outcomes_and_returns_immutable_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guarded = _OutcomeGuard(_row(date(2026, 1, 1), "AAA"))
    guarded.update(
        {
            "net_alpha_percent": 999.0,
            "success_after_cost": False,
            "severe_mae": True,
            "label_end_date": date(2030, 1, 1),
        }
    )
    predictions = iter(((0.4,), (0.8,), (0.25,)))
    monkeypatch.setattr(
        quant_v5_training,
        "_predict_raw",
        lambda model, matrix: next(predictions),
    )

    output = predict_v5_rows(_bundle(), [guarded], stability_age_sessions=0)[0]

    assert output.date == date(2026, 1, 1)
    assert output.symbol == "AAA"
    assert output.raw_rank_score == pytest.approx(0.4)
    assert output.calibrated_outputs == CalibratedOutputs(
        success_after_cost_probability=0.8,
        within_date_alpha_signal=0.5,
        severe_mae_safety_probability=0.75,
    )
    with pytest.raises(FrozenInstanceError):
        output.raw_rank_score = 0.0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        output.calibrated_outputs.within_date_alpha_signal = 0.0  # type: ignore[misc]


def test_platt_calibration_is_deterministic_bounded_and_immutable() -> None:
    probabilities = tuple(0.8 if index % 2 == 0 else 0.2 for index in range(60))
    outcomes = tuple(index % 3 == 0 for index in range(60))

    first = fit_platt_calibrator(probabilities, outcomes)
    second = fit_platt_calibrator(probabilities, outcomes)

    assert first == second
    assert first.samples == 60
    assert 0.0 < first.calibrate(0.0) < 1.0
    assert 0.0 < first.calibrate(1.0) < 1.0
    with pytest.raises(FrozenInstanceError):
        first.slope = 0.0  # type: ignore[misc]


@pytest.mark.parametrize(
    ("train_rows", "calibration_rows", "message"),
    [
        (_training_rows()[:-1], _calibration_rows(), "ranker.*500"),
        (
            [
                {**row, "net_alpha_percent": 2.0, "excess_return_percent": 3.0, "success_after_cost": True}
                for row in _training_rows()
            ],
            _calibration_rows(),
            "success.*class support",
        ),
        (_training_rows(), _calibration_rows()[:-1], "calibration.*60"),
    ],
)
def test_training_fails_closed_on_insufficient_sample_or_class_support(
    train_rows: list[dict[str, Any]],
    calibration_rows: list[dict[str, Any]],
    message: str,
) -> None:
    with pytest.raises(V5TrainingError, match=message):
        fit_v5_model_bundle(train_rows, calibration_rows, stability_age_sessions=0)


def test_training_rejects_rows_shared_between_roles() -> None:
    train_rows = _training_rows()
    calibration_rows = _calibration_rows()
    calibration_rows[0] = dict(train_rows[0])

    with pytest.raises(V5TrainingError, match="overlap"):
        fit_v5_model_bundle(train_rows, calibration_rows, stability_age_sessions=0)


def test_training_rejects_conflicting_net_alpha_fields() -> None:
    train_rows = _training_rows()
    train_rows[0]["excess_return_percent"] = train_rows[0]["net_alpha_percent"] + 9.0

    with pytest.raises(V5TrainingError, match="net alpha fields conflict"):
        fit_v5_model_bundle(train_rows, _calibration_rows(), stability_age_sessions=0)


def test_training_rejects_conflicting_severe_mae_fields() -> None:
    train_rows = _training_rows()
    train_rows[0]["severe_mae"] = False
    train_rows[0]["mae_magnitude_percent"] = 6.0

    with pytest.raises(V5TrainingError, match="severe_mae fields conflict"):
        fit_v5_model_bundle(train_rows, _calibration_rows(), stability_age_sessions=0)


def test_bundle_versions_and_feature_order_come_from_frozen_v5_model_config() -> None:
    bundle = _bundle()
    assert bundle.model_version == V5_CONFIG.model_version
    assert bundle.feature_version == V5_CONFIG.feature_version
    assert bundle.policy_version == V5_CONFIG.policy_version
    assert bundle.feature_names == V5_CONFIG.feature_names
    with pytest.raises(FrozenInstanceError):
        bundle.model_version = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model_version", "stale-model"),
        ("feature_version", "stale-features"),
        ("policy_version", "stale-policy"),
    ],
)
def test_bundle_rejects_stale_lineage(field: str, value: str) -> None:
    values = {
        "ranker": object(),
        "success_classifier": object(),
        "severe_mae_classifier": object(),
        "success_calibrator": PlattCalibrator(0.0, 1.0, 60),
        "severe_mae_calibrator": PlattCalibrator(0.0, 1.0, 60),
        "training_rows": 500,
        "ranker_training_rows": 500,
        "ranker_training_dates": 25,
        "calibration_rows": 60,
        field: value,
    }
    with pytest.raises(ValueError, match="lineage"):
        V5ModelBundle(**values)
