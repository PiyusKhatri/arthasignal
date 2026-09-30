from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from archive.quant_research_v1.pipeline import validate_quant_v5


def test_protocol_rejects_any_fold_count_other_than_four(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        validate_quant_v5,
        "_load_v5_research_rows",
        lambda **_: pytest.fail("invalid protocol must fail before loading data"),
    )

    result = validate_quant_v5.validate_quant_v5(limit=10, folds=3)

    assert result["status"] == "invalid_protocol"
    assert result["requested_folds"] == 3
    assert result["required_folds"] == 4


def test_turnover_percentiles_use_average_ranks_for_ties() -> None:
    trading_date = date(2025, 1, 1)
    rows = [
        {"date": trading_date, "symbol": "A", "trailing_turnover_20d": 10.0},
        {"date": trading_date, "symbol": "B", "trailing_turnover_20d": 20.0},
        {"date": trading_date, "symbol": "C", "trailing_turnover_20d": 20.0},
        {"date": trading_date, "symbol": "D", "trailing_turnover_20d": 40.0},
        {"date": trading_date, "symbol": "E", "trailing_turnover_20d": 50.0},
        {"date": trading_date, "symbol": "F", "trailing_turnover_20d": 60.0},
    ]

    annotated = validate_quant_v5._annotate_turnover_percentiles(rows)
    by_symbol = {row["symbol"]: row for row in annotated}

    assert by_symbol["B"]["turnover_percentile"] == pytest.approx(0.30)
    assert by_symbol["C"]["turnover_percentile"] == pytest.approx(0.30)
    assert by_symbol["A"]["liquidity_bucket"] == "low"
    assert by_symbol["F"]["liquidity_bucket"] == "high"
    assert "turnover_percentile" not in rows[0]


def test_trailing_turnover_uses_exact_market_sessions_and_zero_fills_absence() -> None:
    start = date(2025, 1, 1)
    sessions = [start + timedelta(days=index) for index in range(25)]
    positions = {session: index for index, session in enumerate(sessions)}
    turnover = {session: 100.0 for session in sessions[-20::2]}

    value = validate_quant_v5._trailing_market_session_turnover(
        trading_date=sessions[-1],
        market_dates=sessions,
        market_position=positions,
        turnover_by_date=turnover,
    )

    assert value == pytest.approx(50.0)
    assert validate_quant_v5._trailing_market_session_turnover(
        trading_date=sessions[18],
        market_dates=sessions,
        market_position=positions,
        turnover_by_date=turnover,
    ) is None


def _baseline_row(symbol: str, trading_date: date, score: float) -> dict:
    return {
        "date": trading_date,
        "symbol": symbol,
        "sector": "Finance",
        "features": {
            "relative_strength_market_20d": score,
            "relative_strength_sector_20d": 0.0,
            "return_60d": 0.0,
            "distance_sma50_percent": 0.0,
            "turnover_ratio_20d": 0.0,
            "annualized_volatility_20d": 0.0,
        },
        "regime_context": {
            "market_close": 100.0,
            "market_sma200": 90.0,
            "market_return_20d_percent": 3.0,
            "sector_relative_strength_20d_percent": 0.0,
        },
    }


def test_full_baseline_history_keeps_valid_rows_below_candidate_cut() -> None:
    trading_date = date(2025, 1, 1)
    rows = [
        _baseline_row(f"S{index:02d}", trading_date, float(10 - index))
        for index in range(10)
    ]

    history = validate_quant_v5._build_full_baseline_rank_history(rows)

    assert len(history) == 10
    assert history[0]["baseline_percentile"] == 1.0
    assert history[-1]["baseline_percentile"] == 0.0


def test_matched_baseline_uses_percentile_not_raw_score() -> None:
    trading_date = date(2025, 1, 1)
    rows = [
        {
            "date": trading_date,
            "symbol": "HIGH_PERCENTILE",
            "baseline_percentile": 0.9,
            "baseline_score": -10.0,
            "v4_market_regime": "bull",
        },
        {
            "date": trading_date,
            "symbol": "HIGH_SCORE",
            "baseline_percentile": 0.1,
            "baseline_score": 10.0,
            "v4_market_regime": "bull",
        },
    ]

    selected = validate_quant_v5._matched_percentile_baseline_selection(
        rows, {trading_date: 1}
    )

    assert selected["selected"][0]["row"]["symbol"] == "HIGH_PERCENTILE"


def test_split_manifest_fingerprint_is_deterministic_and_content_addressed() -> None:
    row = {
        "date": date(2025, 1, 1),
        "label_end_date": date(2025, 2, 1),
        "symbol": "AAA",
        "value": 1,
    }
    folds = [{"fold": 1, "train": [row], "calibration": [row], "test": [row]}]

    first = validate_quant_v5._split_manifest_fingerprint([row], folds)
    second = validate_quant_v5._split_manifest_fingerprint([row], folds)
    changed = validate_quant_v5._split_manifest_fingerprint(
        [{**row, "value": 2}],
        [{"fold": 1, "train": [{**row, "value": 2}], "calibration": [row], "test": [row]}]
    )

    assert first == second
    assert first != changed


def test_next_open_replay_returns_complete_three_strategy_metrics() -> None:
    start = date(2025, 1, 1)
    sessions = [start + timedelta(days=index) for index in range(30)]
    signal_date = sessions[0]
    row = {
        "date": signal_date,
        "symbol": "AAA",
        "baseline_percentile": 0.9,
        "v4_market_regime": "bull",
    }
    ranked = {
        "row": row,
        "final_score": 0.9,
        "override_action": "promote",
        "expected_excess_return_percent": 0.0,
        "market_regime": "bull",
    }
    runs = [(signal_date, [ranked])]
    folds = [
        {
            "fold": 1,
            "replay_runs": {"v5": runs, "v41": runs, "baseline": runs},
        }
    ]
    prices = {"AAA": {session: 100.0 + index for index, session in enumerate(sessions)}}

    metrics, status = validate_quant_v5._build_replay_metrics(
        fold_results=folds,
        market_sessions=sessions,
        replay_inputs={"mark_history": prices, "execution_history": prices},
    )

    assert status["status"] == "complete"
    assert metrics.v5_compounded_return_percent is not None
    assert metrics.v41_compounded_return_percent is not None
    assert metrics.baseline_compounded_return_percent is not None
    assert metrics.v5_annualized_turnover is not None


@dataclass(frozen=True)
class _FakeReport:
    marker: str = "report"


@dataclass(frozen=True)
class _FakeGateMetrics:
    marker: str = "metrics"


@dataclass(frozen=True)
class _FakeGate:
    status: str = "research_rejected"


def test_validator_runs_all_four_purged_folds_and_is_json_serializable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = date(2020, 1, 1)
    rows = [
        {
            "date": start + timedelta(days=index),
            "label_end_date": start + timedelta(days=index + 1),
            "symbol": f"S{index}",
        }
        for index in range(20)
    ]
    market_sessions = tuple(row["date"] for row in rows)
    monkeypatch.setattr(
        validate_quant_v5,
        "_load_v5_research_rows",
        lambda **_: (rows, market_sessions, {"labeled": len(rows)}),
    )
    folds = [
        {
            "fold": index,
            "train": rows[:8],
            "calibration": rows[8:12],
            "test": rows[12:16],
        }
        for index in range(1, 5)
    ]
    monkeypatch.setattr(validate_quant_v5, "expanding_nested_folds", lambda *_args, **_kwargs: folds)
    seen: list[int] = []

    def fake_evaluate(fold):
        seen.append(fold["fold"])
        return {
            "fold": fold["fold"],
            "v5_rows": [],
            "v41_rows": [],
            "baseline_rows": [],
            "training_climatology": 0.5,
            "diagnostics": {"ok": True},
        }

    monkeypatch.setattr(validate_quant_v5, "_evaluate_fold", fake_evaluate)
    monkeypatch.setattr(validate_quant_v5, "build_historical_validation_report", lambda **_: _FakeReport())
    monkeypatch.setattr(validate_quant_v5, "derive_historical_gate_metrics", lambda *_: _FakeGateMetrics())
    monkeypatch.setattr(validate_quant_v5, "evaluate_historical_gate", lambda *_: _FakeGate())

    result = validate_quant_v5.validate_quant_v5(limit=20, folds=4)

    assert seen == [1, 2, 3, 4]
    assert result["status"] == "ready"
    assert result["historical_gate"]["status"] == "research_rejected"
    assert result["replay"]["status"] == "missing_replay_data_fail_closed"
    json.dumps(result)


def test_validator_fails_closed_when_any_outer_fold_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {"date": date(2020, 1, 1), "label_end_date": date(2020, 2, 1), "symbol": "A"}
    monkeypatch.setattr(
        validate_quant_v5,
        "_load_v5_research_rows",
        lambda **_: ([row], (row["date"],), {}),
    )
    monkeypatch.setattr(
        validate_quant_v5,
        "expanding_nested_folds",
        lambda *_args, **_kwargs: [{"fold": 1, "train": [row], "calibration": [row], "test": [row]}],
    )

    result = validate_quant_v5.validate_quant_v5(limit=1, folds=4)

    assert result["status"] == "insufficient_valid_folds"
    assert result["valid_folds"] == 1
    assert result["required_folds"] == 4
