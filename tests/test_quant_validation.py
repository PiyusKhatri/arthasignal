from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

from src.pipeline.quant_validation import evaluate_quant_validation_rows


def _row(
    i: int,
    *,
    probability: float,
    success: bool,
    excess: float,
    status: str = "resolved",
) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        probability_outperform=probability,
        success_after_cost=success if status == "resolved" else None,
        realized_excess_return_percent=excess if status == "resolved" else None,
        as_of_date=date(2026, 8, 21) + timedelta(days=i % 20),
        decision="strong_candidate" if probability >= 0.65 else "neutral",
    )


def test_quant_gate_stays_collecting_until_precommitted_sample_sizes_are_met() -> None:
    rows = [_row(i, probability=0.75, success=True, excess=2.0) for i in range(25)]
    status = evaluate_quant_validation_rows(rows)

    assert status["gate_status"] == "collecting"
    assert status["public_high_confidence_enabled"] is False
    assert status["resolved_calls"] == 25


def test_quant_gate_passes_only_when_calibration_and_precision_checks_pass() -> None:
    rows = []
    # 40 high-confidence calls: 30 wins / 10 losses => 75% precision with positive mean excess.
    for i in range(40):
        success = i < 30
        rows.append(
            _row(
                i,
                probability=0.75,
                success=success,
                excess=2.0 if success else -1.0,
            )
        )

    # 40 lower-probability calls keep the global Brier score realistic and below the fixed gate.
    for i in range(40, 80):
        success = i % 2 == 0
        rows.append(
            _row(
                i,
                probability=0.40,
                success=success,
                excess=0.8 if success else -0.8,
            )
        )

    status = evaluate_quant_validation_rows(rows)
    assert status["resolved_calls"] == 80
    assert status["independent_entry_days"] == 20
    assert status["high_confidence_calls"] == 40
    assert status["metrics"]["high_confidence_precision"] == 0.75
    assert status["gate_status"] == "pass"
    assert status["public_high_confidence_enabled"] is True
    assert all(status["pass_checks"].values())


def test_quant_gate_fails_after_sample_ready_when_high_confidence_calls_are_bad() -> None:
    rows = []
    for i in range(40):
        success = i < 16
        rows.append(
            _row(
                i,
                probability=0.75,
                success=success,
                excess=1.5 if success else -2.0,
            )
        )
    for i in range(40, 80):
        success = i % 2 == 0
        rows.append(
            _row(
                i,
                probability=0.45,
                success=success,
                excess=0.5 if success else -0.5,
            )
        )

    status = evaluate_quant_validation_rows(rows)
    assert status["resolved_calls"] == 80
    assert status["high_confidence_calls"] == 40
    assert status["gate_status"] == "fail"
    assert status["public_high_confidence_enabled"] is False
    assert status["pass_checks"]["high_confidence_precision"] is False


def test_pending_and_void_rows_never_count_as_resolved_evidence() -> None:
    rows = [
        _row(0, probability=0.8, success=True, excess=3.0, status="pending"),
        _row(1, probability=0.8, success=True, excess=3.0, status="void"),
        _row(2, probability=0.8, success=True, excess=3.0, status="resolved"),
    ]
    status = evaluate_quant_validation_rows(rows)

    assert status["resolved_calls"] == 1
    assert status["pending_calls"] == 1
    assert status["void_calls"] == 1
    assert status["gate_status"] == "collecting"
