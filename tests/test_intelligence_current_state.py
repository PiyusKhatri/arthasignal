from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from src.services.stock_intelligence import _active_validation_signal_names, _build_payload


def _technical(**overrides):
    values = {
        "date": date(2026, 8, 20),
        "sma_50": 500.0,
        "sma_200": 450.0,
        "rsi_14": 55.0,
        "macd_line": 10.0,
        "macd_signal": 8.0,
        "bollinger_lower": 420.0,
        "doji": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_current_signal_state_is_evaluated_from_latest_technical_snapshot() -> None:
    active = _active_validation_signal_names(
        _technical(rsi_14=25.0),
        price=500.0,
        liquidity="high_liquidity",
    )
    assert "rsi_14 < 30 (oversold)" in active
    assert "rsi_14 > 70 (overbought)" not in active


def test_doji_current_signal_respects_precommitted_liquidity_rule() -> None:
    technical = _technical(doji=True)
    assert "doji" not in _active_validation_signal_names(
        technical,
        price=500.0,
        liquidity="low_liquidity",
    )
    assert "doji" in _active_validation_signal_names(
        technical,
        price=500.0,
        liquidity="high_liquidity",
    )


def test_open_validation_call_does_not_override_current_signal_state() -> None:
    old_pending_call = SimpleNamespace(
        signal_name="doji",
        status="pending",
        entry_date=date(2026, 8, 1),
        forward_days_horizon=20,
    )
    current_signal = "rsi_14 < 30 (oversold)"

    payload = _build_payload(
        symbol="TEST",
        company_name="Test Corp",
        sector="Commercial Banks",
        technical=_technical(rsi_14=25.0),
        fundamental=None,
        price=500.0,
        liquidity="high_liquidity",
        calls=[old_pending_call],
        active_signal_names={current_signal},
        confidence_rows=[],
        backtests=[],
        market_regime={
            "state": "mixed",
            "risk_level": "low",
            "confidence": 90,
            "score_adjustment": 0,
            "return_20d_percent": 0.0,
        },
        validation_status={"policy_version": "test", "signals": {}},
        include_ai=False,
    )

    assert [row["signal_name"] for row in payload["signals"]] == [current_signal]
    assert payload["signals"][0]["status"] == "active"
    assert payload["signals"][0]["entry_date"] is None
    assert "doji" not in {row["signal_name"] for row in payload["signals"]}
