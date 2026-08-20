from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.services.quant_execution_aware import (
    EXECUTION_HURDLE_PERCENT,
    SEVERE_DRAWDOWN_THRESHOLD_PERCENT,
    execution_aware_scores,
    expanding_nested_folds,
    relabel_downside_rows,
    relabel_execution_rows,
    selected_downside_rate,
)


def _rows(days: int = 420, names: int = 12) -> list[dict]:
    start = date(2020, 1, 1)
    rows: list[dict] = []
    for day in range(days):
        trading_date = start + timedelta(days=day)
        for rank in range(names):
            excess = 4.0 if rank < 4 else (1.4 if rank < 8 else -2.0)
            rows.append(
                {
                    "date": trading_date,
                    "label_end_date": trading_date + timedelta(days=20),
                    "symbol": f"S{rank:02d}",
                    "sector": "Bank" if rank % 2 == 0 else "Hydro",
                    "excess_return_percent": excess,
                    "stock_return_percent": excess + 1.0,
                    "market_return_percent": 1.0,
                    "max_adverse_percent": -2.0 if rank < 6 else -7.0,
                    "features": {},
                    "success": excess > 0.5,
                }
            )
    return rows


def test_execution_target_uses_stricter_one_percent_hurdle() -> None:
    rows = [
        {"excess_return_percent": 0.9, "max_adverse_percent": -2.0},
        {"excess_return_percent": 1.1, "max_adverse_percent": -2.0},
    ]
    relabeled = relabel_execution_rows(rows)
    assert relabeled[0]["success"] is False
    assert relabeled[1]["success"] is True
    assert relabeled[1]["execution_hurdle_percent"] == pytest.approx(EXECUTION_HURDLE_PERCENT)


def test_downside_target_marks_only_severe_adverse_excursions() -> None:
    rows = [
        {"excess_return_percent": 2.0, "max_adverse_percent": -4.9},
        {"excess_return_percent": 2.0, "max_adverse_percent": -5.1},
    ]
    relabeled = relabel_downside_rows(rows)
    assert relabeled[0]["success"] is False
    assert relabeled[1]["success"] is True
    assert relabeled[1]["downside_threshold_percent"] == pytest.approx(SEVERE_DRAWDOWN_THRESHOLD_PERCENT)


def test_nested_folds_purge_forward_labels_at_every_boundary() -> None:
    folds = expanding_nested_folds(_rows(), folds=4)
    assert len(folds) >= 3
    seen_test_dates: set[date] = set()

    for fold in folds:
        calibration_start = fold["calibration_start"]
        test_start = fold["test_start"]
        assert max(row["label_end_date"] for row in fold["train"]) < calibration_start
        assert max(row["label_end_date"] for row in fold["calibration"]) < test_start
        assert max(row["date"] for row in fold["train"]) < calibration_start
        assert max(row["date"] for row in fold["calibration"]) < test_start

        test_dates = {row["date"] for row in fold["test"]}
        assert seen_test_dates.isdisjoint(test_dates)
        seen_test_dates |= test_dates


def test_execution_aware_score_rewards_edge_and_penalizes_downside() -> None:
    rows = [
        {"date": date(2026, 1, 1)},
        {"date": date(2026, 1, 1)},
        {"date": date(2026, 1, 1)},
    ]
    execution = [0.70, 0.70, 0.55]
    risk = [0.15, 0.80, 0.15]
    rank = [3.0, 3.0, 1.0]
    scores = execution_aware_scores(rows, execution, risk, rank)
    assert len(scores) == 3
    assert scores[0] > scores[1]  # same edge/rank, much safer downside profile
    assert scores[0] > scores[2]  # safer and stronger edge/rank


def test_selected_downside_rate_detects_risk_reduction() -> None:
    rows = []
    scores = []
    trading_date = date(2026, 1, 1)
    for index in range(20):
        safe = index < 10
        rows.append(
            {
                "date": trading_date,
                "max_adverse_percent": -2.0 if safe else -8.0,
            }
        )
        scores.append(10.0 if safe else 0.0)

    result = selected_downside_rate(rows, scores, k=10)
    assert result["universe_severe_drawdown_rate"] == pytest.approx(0.5)
    assert result["selected_severe_drawdown_rate"] == pytest.approx(0.0)
    assert result["relative_risk_reduction"] == pytest.approx(1.0)
