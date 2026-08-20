from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# This protocol intentionally starts at the commit that formalized the forward
# validation design. Calls before this date may remain useful diagnostically but
# MUST NOT be used as launch-gate evidence because the statistical gate had not
# yet been pre-committed.
VALIDATION_POLICY_VERSION = "2026-08-20-v1"
VALIDATION_PROTOCOL_START_DATE = date(2026, 8, 20)

# Pre-committed round-trip cost assumption used only by the validation gate.
# 50 bps matches the planning document's 0.4-0.5% working fee estimate. It is a
# protocol parameter, not a claim that every NEPSE trade costs exactly this
# amount. Changing it requires a new policy version and a new start date.
ROUND_TRIP_COST_BPS = 50

# The gate uses entry-date clusters rather than treating simultaneous calls on
# many symbols as independent observations. Twenty independent trading-day
# clusters is the minimum before the normal-approximation confidence interval is
# allowed to make a pass/fail decision.
MIN_INDEPENDENT_ENTRY_DAYS = 20
CONFIDENCE_Z_95 = 1.959963984540054


@dataclass(frozen=True)
class ValidationSignalSpec:
    signal_name: str
    horizon_trading_days: int
    min_graded_calls: int
    required_liquidity_tier: str | None = None


# Minimum graded-call thresholds are frequency-tier calibrated from the
# historical firing-rate ranges already recorded in the approved design doc:
# Bollinger-lower is the high-frequency tier; the other three are the medium
# frequency tier. If a threshold is not reached in 4-6 weeks, the window extends
# rather than lowering the threshold after observing results.
VALIDATION_SIGNAL_SPECS: dict[str, ValidationSignalSpec] = {
    "rsi_14 < 30 (oversold)": ValidationSignalSpec(
        signal_name="rsi_14 < 30 (oversold)",
        horizon_trading_days=20,
        min_graded_calls=60,
    ),
    "close < bollinger_lower": ValidationSignalSpec(
        signal_name="close < bollinger_lower",
        horizon_trading_days=20,
        min_graded_calls=100,
    ),
    "rsi_14 > 70 (overbought)": ValidationSignalSpec(
        signal_name="rsi_14 > 70 (overbought)",
        horizon_trading_days=20,
        min_graded_calls=60,
    ),
    "doji": ValidationSignalSpec(
        signal_name="doji",
        horizon_trading_days=20,
        min_graded_calls=60,
        required_liquidity_tier="high_liquidity",
    ),
}

TARGET_SIGNAL_HORIZONS = {
    signal_name: spec.horizon_trading_days for signal_name, spec in VALIDATION_SIGNAL_SPECS.items()
}


def cost_percent() -> float:
    return ROUND_TRIP_COST_BPS / 100.0
