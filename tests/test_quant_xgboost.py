from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.services import quant_cross_sectional
from src.services.quant_xgboost import (
    calibrate_probability,
    chronological_three_way_split,
    fit_platt_calibrator,
    precision_at_k_by_date,
)


def _dated_rows(days: int = 100) -> list[dict]:
    start = date(2020, 1, 1)
    rows = []
    for index in range(days):
        trading_date = start + timedelta(days=index)
        rows.append(
            {
                "date": trading_date,
                "label_end_date": trading_date + timedelta(days=20),
                "success": index % 2 == 0,
                "excess_return_percent": 2.0 if index % 2 == 0 else -1.0,
                "features": {},
            }
        )
    return rows


def test_chronological_split_purges_overlapping_forward_labels() -> None:
    split = chronological_three_way_split(_dated_rows(120))
    assert split["train"] and split["calibration"] and split["test"]

    calibration_start = min(row["date"] for row in split["calibration"])
    test_start = min(row["date"] for row in split["test"])

    assert max(row["label_end_date"] for row in split["train"]) < calibration_start
    assert max(row["label_end_date"] for row in split["calibration"]) < test_start
    assert max(row["date"] for row in split["train"]) < calibration_start
    assert max(row["date"] for row in split["calibration"]) < test_start


def test_platt_calibrator_is_bounded_and_uses_held_out_outcomes() -> None:
    probabilities = []
    outcomes = []
    for index in range(120):
        # Deliberately over-confident predictions: 0.9 group succeeds only 70%,
        # 0.1 group succeeds 30%. Calibration should pull extremes inward.
        high = index < 60
        probabilities.append(0.9 if high else 0.1)
        if high:
            outcomes.append(index % 10 < 7)
        else:
            outcomes.append(index % 10 < 3)

    calibrator = fit_platt_calibrator(probabilities, outcomes)
    assert calibrator is not None
    high = calibrate_probability(0.9, calibrator)
    low = calibrate_probability(0.1, calibrator)
    assert high is not None and low is not None
    assert 0.5 < high < 0.9
    assert 0.1 < low < 0.5


def test_precision_at_k_scores_only_top_ranked_names_per_date() -> None:
    rows = []
    scores = []
    start = date(2025, 1, 1)
    for day in range(4):
        trading_date = start + timedelta(days=day)
        for rank in range(5):
            rows.append(
                {
                    "date": trading_date,
                    "success": rank < 2,
                    "excess_return_percent": 4.0 if rank < 2 else -2.0,
                }
            )
            scores.append(float(5 - rank))

    result = precision_at_k_by_date(rows, scores, k=2)
    assert result["dates"] == 4
    assert result["p_at_k"] == pytest.approx(1.0)
    assert result["mean_top_k_excess_return_percent"] == pytest.approx(4.0)


def _base_research() -> dict:
    return {
        "decision": {
            "research_label": "neutral",
            "public_label": "research_only",
            "public_eligible": False,
            "probability_outperform_nepse_after_cost": 0.58,
            "confidence_score": 55,
        },
        "probability_model": {
            "probability_outperform_after_cost": 0.58,
            "confidence_score": 55,
            "components": {"ridge_logistic_probability": 0.60},
        },
        "historical_analogs": {
            "probability_outperform_after_cost": 0.62,
            "expected_excess_return_percent": 2.5,
        },
        "confluence": {
            "supportive_dimensions": 7,
            "total_dimensions": 9,
            "support_ratio": 7 / 9,
            "dimensions": {"historical_probability": True},
        },
        "market_regime": {"state": "bull"},
        "sector_regime": {"state": "bull"},
        "liquidity": {"quality": "high"},
        "event_risk": {"level": "low"},
        "forward_validation": {"public_high_confidence_enabled": False},
    }


def test_unpromoted_xgboost_challenger_cannot_change_live_research(monkeypatch: pytest.MonkeyPatch) -> None:
    research = _base_research()
    original_probability = research["decision"]["probability_outperform_nepse_after_cost"]
    monkeypatch.setattr(
        quant_cross_sectional,
        "build_cross_sectional_prediction",
        lambda _session, _symbol: {
            "available": False,
            "candidate_available": True,
            "promotable": False,
            "calibrated_probability": 0.91,
            "rank_score": 3.2,
            "reason": "challenger_not_promoted",
        },
    )

    enhanced = quant_cross_sectional.enhance_quant_research_with_cross_sectional(object(), "TEST", research)
    assert enhanced is research
    assert enhanced["cross_sectional_ml"]["candidate_available"] is True
    assert enhanced["decision"]["probability_outperform_nepse_after_cost"] == original_probability
    assert enhanced["probability_model"]["model_type"] if "model_type" in enhanced["probability_model"] else True


def test_promoted_model_can_enhance_research_but_not_bypass_forward_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    research = _base_research()
    monkeypatch.setattr(
        quant_cross_sectional,
        "build_cross_sectional_prediction",
        lambda _session, _symbol: {
            "available": True,
            "candidate_available": True,
            "promotable": True,
            "calibrated_probability": 0.82,
            "rank_score": 4.0,
            "model_version": "artha-xgb-cross-sectional-v1",
            "trained_through": "2026-08-20",
            "holdout_metrics": {
                "calibrated_classifier": {"high_confidence_precision": 0.72}
            },
        },
    )

    enhanced = quant_cross_sectional.enhance_quant_research_with_cross_sectional(object(), "TEST", research)
    assert enhanced is not None
    probability = enhanced["decision"]["probability_outperform_nepse_after_cost"]
    assert probability > 0.58
    assert enhanced["probability_model"]["model_type"] == "promoted_cross_sectional_xgboost_analogue_ridge_ensemble"
    assert enhanced["decision"]["public_label"] == "research_only"
    assert enhanced["decision"]["public_eligible"] is False
