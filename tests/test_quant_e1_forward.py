from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

from src.services.quant_e1_forward import _prediction_mismatch, shift_signal_decisions_to_next_session
from src.services.quant_execution_policy_e1 import simulate_execution_policy_e1


def _candidate(symbol: str, trading_date: date) -> dict:
    return {
        "row": {
            "symbol": symbol,
            "date": trading_date,
            "baseline_rank": 1,
            "baseline_percentile": 0.95,
            "baseline_score": 1.2,
            "v4_market_regime": "bull",
            "liquidity_bucket": "high",
        },
        "final_score": 0.90,
        "override_action": "hold",
        "expected_excess_return_percent": 2.0,
        "market_regime": "bull",
        "sector_regime": "leading",
    }


def test_signal_decisions_shift_to_next_market_session_and_keep_abstention() -> None:
    d1 = date(2026, 8, 20)
    d2 = d1 + timedelta(days=1)
    d3 = d2 + timedelta(days=3)
    shifted = shift_signal_decisions_to_next_session(
        [(d1, []), (d2, [_candidate("AAA", d2)])],
        [d1, d2, d3],
    )
    assert d2 in shifted
    assert shifted[d2] == []
    assert d3 in shifted
    assert shifted[d3][0]["row"]["signal_date"] == d2
    assert shifted[d3][0]["row"]["date"] == d3


def test_forward_replay_uses_separate_execution_open_and_close_mark() -> None:
    d1 = date(2026, 8, 20)
    d2 = d1 + timedelta(days=1)
    candidate = _candidate("AAA", d2)
    result = simulate_execution_policy_e1(
        market_dates=[d1, d2],
        predictions_by_date={d2: [candidate]},
        price_history={"AAA": {d2: 100.0}},
        execution_price_history={"AAA": {d2: 80.0}},
        terminal_liquidation=False,
        mode="v41",
        max_positions=1,
    )
    buys = [trade for trade in result["trade_log"] if trade["side"] == "buy"]
    assert len(buys) == 1
    assert buys[0]["execution_price"] == 80.0
    assert result["terminal_liquidation"] is False


def test_prediction_crosscheck_detects_changed_frozen_score() -> None:
    trading_date = date(2026, 8, 20)
    prediction = _candidate("AAA", trading_date)
    stored = {
        "AAA": SimpleNamespace(
            override_action="hold",
            final_score=0.90,
            expected_excess_return_percent=2.0,
        )
    }
    assert _prediction_mismatch([prediction], stored) is None
    stored["AAA"].final_score = 0.80
    assert _prediction_mismatch([prediction], stored) == "final_score_mismatch:AAA"
