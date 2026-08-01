from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from src.pipeline.fundamental_ratios import payout_ratio

TEN_DP = Decimal("0.0000000001")


def q(value):
    return value.quantize(TEN_DP, rounding=ROUND_HALF_UP)


def test_payout_ratio_normal():
    result = payout_ratio(Decimal("10.00"), Decimal("25.00"))
    assert q(result) == Decimal("40.0000000000")


def test_payout_ratio_low():
    result = payout_ratio(Decimal("5.00"), Decimal("50.00"))
    assert q(result) == Decimal("10.0000000000")


def test_payout_ratio_exactly_full():
    result = payout_ratio(Decimal("20.00"), Decimal("20.00"))
    assert q(result) == Decimal("100.0000000000")


def test_payout_ratio_over_full_distribution_is_computed_not_capped():
    result = payout_ratio(Decimal("30.00"), Decimal("20.00"))
    assert q(result) == Decimal("150.0000000000")


def test_payout_ratio_zero_dividend():
    result = payout_ratio(Decimal("0.00"), Decimal("15.00"))
    assert q(result) == Decimal("0.0000000000")


def test_payout_ratio_zero_eps_returns_none():
    result = payout_ratio(Decimal("5.00"), Decimal("0.00"))
    assert result is None


def test_payout_ratio_negative_eps_returns_none_not_misleading_ratio():
    result = payout_ratio(Decimal("5.00"), Decimal("-10.00"))
    naive_result = Decimal("5.00") / Decimal("-10.00") * 100
    assert naive_result == Decimal("-50.00")
    assert result is None


def test_payout_ratio_negative_eps_zero_dividend_returns_none():
    result = payout_ratio(Decimal("0.00"), Decimal("-5.00"))
    assert result is None
