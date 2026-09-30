from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.api.portfolio import HoldingCreate, HoldingUpdate
from src.api.security import hash_password, verify_password
from src.pipeline.grade_signal_calls import (
    _find_resolution_price,
    _has_corporate_action,
    _resolution_target_date,
    _void_cutoff_date,
)
from src.pipeline.signal_validation_policy import (
    VALIDATION_PROTOCOL_START_DATE,
    VALIDATION_SIGNAL_SPECS,
)


def test_bcrypt_does_not_accept_password_suffix_after_72_bytes() -> None:
    exact = "a" * 71 + "1"
    hashed = hash_password(exact)
    assert verify_password(exact, hashed)
    assert not verify_password(exact + "attacker-controlled-suffix", hashed)


def test_bcrypt_rejects_passwords_over_72_bytes() -> None:
    with pytest.raises(ValueError):
        hash_password("a" * 72 + "1")


def test_holding_validation_rejects_non_positive_values_and_future_date() -> None:
    with pytest.raises(ValidationError):
        HoldingCreate(symbol="NABIL", quantity=Decimal("0"), purchase_price=Decimal("100"), purchase_date=date.today())
    with pytest.raises(ValidationError):
        HoldingCreate(symbol="NABIL", quantity=Decimal("1"), purchase_price=Decimal("-1"), purchase_date=date.today())
    with pytest.raises(ValidationError):
        HoldingCreate(
            symbol="NABIL",
            quantity=Decimal("1"),
            purchase_price=Decimal("100"),
            purchase_date=date.today() + timedelta(days=1),
        )


def test_holding_symbol_is_normalized() -> None:
    holding = HoldingCreate(
        symbol=" nabil ", quantity=Decimal("1"), purchase_price=Decimal("100"), purchase_date=date.today()
    )
    assert holding.symbol == "NABIL"


def test_holding_update_rejects_zero_quantity() -> None:
    with pytest.raises(ValidationError):
        HoldingUpdate(quantity=Decimal("0"))


def test_resolution_uses_trading_days_and_void_grace_window() -> None:
    trading_days = [date(2026, 1, day) for day in range(1, 11)]
    assert _resolution_target_date(date(2026, 1, 1), 3, trading_days) == date(2026, 1, 4)
    assert _void_cutoff_date(date(2026, 1, 4), trading_days) == date(2026, 1, 7)


def test_resolution_price_never_reads_beyond_effective_cutoff() -> None:
    index = {
        "NABIL": (
            [date(2026, 1, 5), date(2026, 1, 8)],
            [Decimal("100"), Decimal("110")],
        )
    }
    assert _find_resolution_price(index, "NABIL", date(2026, 1, 6), date(2026, 1, 7)) is None
    assert _find_resolution_price(index, "NABIL", date(2026, 1, 6), date(2026, 1, 8)) == (
        date(2026, 1, 8),
        Decimal("110"),
    )


def test_corporate_action_check_is_call_window_specific() -> None:
    actions = {"NABIL": [date(2026, 1, 5), date(2026, 2, 1)]}
    assert _has_corporate_action(actions, "NABIL", date(2026, 1, 1), date(2026, 1, 10))
    assert not _has_corporate_action(actions, "NABIL", date(2026, 1, 6), date(2026, 1, 20))


def test_forward_validation_policy_is_precommitted_and_consistent() -> None:
    assert VALIDATION_PROTOCOL_START_DATE == date(2026, 8, 20)
    assert set(VALIDATION_SIGNAL_SPECS) == {
        "rsi_14 < 30 (oversold)",
        "close < bollinger_lower",
        "rsi_14 > 70 (overbought)",
        "doji",
    }
    assert all(spec.horizon_trading_days == 20 for spec in VALIDATION_SIGNAL_SPECS.values())
    assert VALIDATION_SIGNAL_SPECS["doji"].required_liquidity_tier == "high_liquidity"
