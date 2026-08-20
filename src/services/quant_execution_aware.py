from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any, Sequence

EXECUTION_HURDLE_PERCENT = 1.00
SEVERE_DRAWDOWN_THRESHOLD_PERCENT = -5.00
OUTER_FOLDS = 4
MIN_FOLD_DATES = 80


def relabel_execution_rows(
    rows: Sequence[dict[str, Any]],
    *,
    hurdle_percent: float = EXECUTION_HURDLE_PERCENT,
) -> list[dict[str, Any]]:
    """Copy rows with a stricter after-cost outperformance target."""
    return [
        {
            **row,
            "success": float(row["excess_return_percent"]) > hurdle_percent,
            "execution_hurdle_percent": hurdle_percent,
        }
        for row in rows
    ]


def relabel_downside_rows(
    rows: Sequence[dict[str, Any]],
    *,
    threshold_percent: float = SEVERE_DRAWDOWN_THRESHOLD_PERCENT,
) -> list[dict[str, Any]]:
    """Copy rows with `success=True` meaning a severe adverse excursion occurred."""
    return [
        {
            **row,
            "success": float(row.get("max_adverse_percent") or 0.0) <= threshold_percent,
            "downside_threshold_percent": threshold_percent,
        }
        for row in rows
    ]


def expanding_nested_folds(
    rows: Sequence[dict[str, Any]],
    *,
    folds: int = OUTER_FOLDS,
) -> list[dict[str, Any]]:
    """Create expanding outer folds with a purged calibration window.

    No train row is retained unless its full forward label ended before the
    calibration period. No calibration row is retained unless its label ended
    before the outer test period. Each test date appears in at most one fold.
    """
    dates = sorted({row["date"] for row in rows})
    if len(dates) < MIN_FOLD_DATES * 2:
        return []

    folds = max(2, int(folds))
    first_test_index = max(MIN_FOLD_DATES, int(len(dates) * 0.55))
    remaining = len(dates) - first_test_index
    test_width = max(MIN_FOLD_DATES, remaining // folds)
    result: list[dict[str, Any]] = []

    for fold_index in range(folds):
        test_start_index = first_test_index + fold_index * test_width
        if test_start_index >= len(dates):
            break
        test_end_index = len(dates) if fold_index == folds - 1 else min(len(dates), test_start_index + test_width)
        if test_end_index - test_start_index < max(20, MIN_FOLD_DATES // 2):
            continue

        calibration_start_index = max(MIN_FOLD_DATES, test_start_index - test_width)
        calibration_start = dates[calibration_start_index]
        test_start = dates[test_start_index]
        test_end_exclusive = dates[test_end_index] if test_end_index < len(dates) else None

        train = [
            row
            for row in rows
            if row["date"] < calibration_start
            and row.get("label_end_date") is not None
            and row["label_end_date"] < calibration_start
        ]
        calibration = [
            row
            for row in rows
            if calibration_start <= row["date"] < test_start
            and row.get("label_end_date") is not None
            and row["label_end_date"] < test_start
        ]
        test = [
            row
            for row in rows
            if row["date"] >= test_start
            and (test_end_exclusive is None or row["date"] < test_end_exclusive)
        ]
        if not train or not calibration or not test:
            continue

        result.append(
            {
                "fold": len(result) + 1,
                "train": train,
                "calibration": calibration,
                "test": test,
                "calibration_start": calibration_start,
                "test_start": test_start,
                "test_end": max(row["date"] for row in test),
            }
        )
    return result


def _date_percentiles(rows: Sequence[dict[str, Any]], scores: Sequence[float]) -> list[float]:
    grouped: dict[Any, list[tuple[int, float]]] = defaultdict(list)
    for index, (row, score) in enumerate(zip(rows, scores)):
        grouped[row["date"]].append((index, float(score)))

    percentiles = [0.5] * len(rows)
    for pairs in grouped.values():
        ordered = sorted(pairs, key=lambda item: item[1])
        denominator = max(1, len(ordered) - 1)
        for position, (original_index, _) in enumerate(ordered):
            percentiles[original_index] = position / denominator
    return percentiles


def execution_aware_scores(
    rows: Sequence[dict[str, Any]],
    execution_probabilities: Sequence[float],
    downside_probabilities: Sequence[float],
    rank_scores: Sequence[float],
) -> list[float]:
    """Combine independent views using a fixed ex-ante policy.

    Weights are intentionally fixed rather than tuned on the historical test
    result: 60% execution probability, 25% cross-sectional rank percentile, and
    15% downside safety (1 - severe-drawdown probability).
    """
    if not (
        len(rows)
        == len(execution_probabilities)
        == len(downside_probabilities)
        == len(rank_scores)
    ):
        return []

    rank_percentiles = _date_percentiles(rows, rank_scores)
    return [
        0.60 * float(exec_probability)
        + 0.25 * float(rank_percentile)
        + 0.15 * (1.0 - float(risk_probability))
        for exec_probability, rank_percentile, risk_probability in zip(
            execution_probabilities,
            rank_percentiles,
            downside_probabilities,
        )
    ]


def selected_downside_rate(
    rows: Sequence[dict[str, Any]],
    scores: Sequence[float],
    *,
    k: int = 10,
    threshold_percent: float = SEVERE_DRAWDOWN_THRESHOLD_PERCENT,
) -> dict[str, Any]:
    grouped: dict[Any, list[tuple[dict[str, Any], float]]] = defaultdict(list)
    for row, score in zip(rows, scores):
        grouped[row["date"]].append((row, float(score)))

    selected: list[dict[str, Any]] = []
    eligible_dates = 0
    for date_rows in grouped.values():
        if len(date_rows) < k:
            continue
        eligible_dates += 1
        selected.extend(row for row, _ in sorted(date_rows, key=lambda item: item[1], reverse=True)[:k])

    universe_rate = (
        mean(1.0 if float(row.get("max_adverse_percent") or 0.0) <= threshold_percent else 0.0 for row in rows)
        if rows
        else None
    )
    selected_rate = (
        mean(1.0 if float(row.get("max_adverse_percent") or 0.0) <= threshold_percent else 0.0 for row in selected)
        if selected
        else None
    )
    return {
        "threshold_percent": threshold_percent,
        "eligible_dates": eligible_dates,
        "selected_rows": len(selected),
        "universe_severe_drawdown_rate": universe_rate,
        "selected_severe_drawdown_rate": selected_rate,
        "relative_risk_reduction": (
            1.0 - selected_rate / universe_rate
            if selected_rate is not None and universe_rate not in (None, 0.0)
            else None
        ),
    }
