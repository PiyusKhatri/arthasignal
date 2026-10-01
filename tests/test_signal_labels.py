from __future__ import annotations

import src.pipeline.signal_labels as labels

STATUS = {
    "signals": {
        "rsi_14 < 30 (oversold)": {
            "gate_status": "collecting",
            "graded_calls": 42,
            "min_graded_calls": 60,
            "independent_entry_days": 4,
            "min_independent_entry_days": 20,
        }
    }
}


def test_backtest_tier_never_reaches_the_public_tier() -> None:
    evidence = labels.evidence_from_status(STATUS)

    label = labels.public_signal_label("rsi_14 < 30 (oversold)", "high_confidence", evidence)

    assert label["tier"] == "under_validation"
    assert label["backtest_tier"] == "high_confidence"
    assert label["validation"] == {
        "status": "collecting",
        "graded_calls": 42,
        "required_calls": 60,
        "independent_entry_days": 4,
        "required_entry_days": 20,
    }


def test_signals_without_forward_calls_show_zero_against_the_policy_minimum() -> None:
    evidence = labels.evidence_from_status(None)

    label = labels.public_signal_label("close < bollinger_lower", "high_confidence", evidence)

    assert label["tier"] == "under_validation"
    assert label["validation"]["graded_calls"] == 0
    assert label["validation"]["required_calls"] == 100


def test_unknown_signal_is_still_under_validation() -> None:
    label = labels.public_signal_label("macd_cross", "high_confidence", {})

    assert label["tier"] == "under_validation"
    assert label["validation"]["graded_calls"] == 0


def test_evidence_falls_back_when_validation_status_cannot_load(monkeypatch) -> None:
    import src.pipeline.validation_status as validation_status

    monkeypatch.setattr(validation_status, "build_validation_status", lambda: (_ for _ in ()).throw(RuntimeError))
    monkeypatch.setitem(labels._cache, "evidence", None)

    evidence = labels.load_signal_evidence()

    assert evidence["doji"]["graded_calls"] == 0
    monkeypatch.setitem(labels._cache, "evidence", None)
