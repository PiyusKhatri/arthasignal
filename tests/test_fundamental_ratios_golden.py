from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from src.pipeline.fundamental_ratios import (
    debt_to_equity,
    eps_growth_rate,
    pe_ratio,
    sector_relative_valuation,
)

TEN_DP = Decimal("0.0000000001")


def q(value):
    return value.quantize(TEN_DP, rounding=ROUND_HALF_UP)


def test_eps_growth_rate_yoy_positive():
    result = eps_growth_rate(Decimal("12.50"), Decimal("10.00"))
    assert q(result) == Decimal("25.0000000000")


def test_eps_growth_rate_qoq_decline():
    result = eps_growth_rate(Decimal("6.00"), Decimal("8.00"))
    assert q(result) == Decimal("-25.0000000000")


def test_eps_growth_rate_recovery_from_loss_uses_abs_denominator():
    result = eps_growth_rate(Decimal("3.00"), Decimal("-5.00"))
    assert q(result) == Decimal("160.0000000000")


def test_eps_growth_rate_recovery_from_loss_would_be_wrong_without_abs():
    result = eps_growth_rate(Decimal("3.00"), Decimal("-5.00"))
    wrong_without_abs = (Decimal("3.00") - Decimal("-5.00")) / Decimal("-5.00") * 100
    assert wrong_without_abs == Decimal("-160.00")
    assert q(result) != q(wrong_without_abs)


def test_eps_growth_rate_loss_deepening():
    result = eps_growth_rate(Decimal("-6.00"), Decimal("-4.00"))
    assert q(result) == Decimal("-50.0000000000")


def test_eps_growth_rate_loss_shrinking():
    result = eps_growth_rate(Decimal("-4.00"), Decimal("-10.00"))
    assert q(result) == Decimal("60.0000000000")


def test_eps_growth_rate_prior_zero_returns_none():
    result = eps_growth_rate(Decimal("5.00"), Decimal("0.00"))
    assert result is None


def test_pe_ratio_normal():
    result = pe_ratio(Decimal("360.00"), Decimal("15.00"))
    assert q(result) == Decimal("24.0000000000")


def test_pe_ratio_near_zero_eps_produces_extreme_value():
    result = pe_ratio(Decimal("100.00"), Decimal("0.03"))
    assert q(result) == Decimal("3333.3333333333")


def test_pe_ratio_zero_eps_returns_none():
    result = pe_ratio(Decimal("100.00"), Decimal("0.00"))
    assert result is None


def test_pe_ratio_negative_eps_returns_none_not_negative_ratio():
    result = pe_ratio(Decimal("200.00"), Decimal("-10.00"))
    assert result is None


def test_sector_relative_valuation_cheap_vs_sector():
    result = sector_relative_valuation(Decimal("12.00"), Decimal("20.00"))
    assert q(result) == Decimal("-40.0000000000")


def test_sector_relative_valuation_expensive_vs_sector():
    result = sector_relative_valuation(Decimal("30.00"), Decimal("20.00"))
    assert q(result) == Decimal("50.0000000000")


def test_sector_relative_valuation_company_pe_none_returns_none():
    result = sector_relative_valuation(None, Decimal("20.00"))
    assert result is None


def test_sector_relative_valuation_sector_avg_pe_none_returns_none():
    result = sector_relative_valuation(Decimal("15.00"), None)
    assert result is None


def test_sector_relative_valuation_sector_avg_pe_zero_returns_none():
    result = sector_relative_valuation(Decimal("15.00"), Decimal("0.00"))
    assert result is None


def test_debt_to_equity_normal():
    result = debt_to_equity(Decimal("400.00"), Decimal("1000.00"))
    assert q(result) == Decimal("0.4000000000")


def test_debt_to_equity_high_leverage():
    result = debt_to_equity(Decimal("1500.00"), Decimal("500.00"))
    assert q(result) == Decimal("3.0000000000")


def test_debt_to_equity_zero_equity_returns_none():
    result = debt_to_equity(Decimal("500.00"), Decimal("0.00"))
    assert result is None


def test_debt_to_equity_negative_equity_returns_none_not_negative_ratio():
    result = debt_to_equity(Decimal("800.00"), Decimal("-200.00"))
    naive_negative_result = Decimal("800.00") / Decimal("-200.00")
    assert naive_negative_result == Decimal("-4.00")
    assert result is None
