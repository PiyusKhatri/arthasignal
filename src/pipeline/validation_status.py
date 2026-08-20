from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from statistics import mean, stdev
from typing import Any

from sqlalchemy import select

from src.database.connection import get_session
from src.database.models import SignalCall, SignalCallStatus
from src.pipeline.signal_validation_policy import (
    CONFIDENCE_Z_95,
    MIN_INDEPENDENT_ENTRY_DAYS,
    ROUND_TRIP_COST_BPS,
    VALIDATION_POLICY_VERSION,
    VALIDATION_PROTOCOL_START_DATE,
    VALIDATION_SIGNAL_SPECS,
    cost_percent,
)


def _mean_ci_95(values: list[float]) -> tuple[float | None, float | None, float | None]:
    if not values:
        return None, None, None
    center = mean(values)
    if len(values) < 2:
        return center, None, None
    standard_error = stdev(values) / math.sqrt(len(values))
    margin = CONFIDENCE_Z_95 * standard_error
    return center, center - margin, center + margin


def _load_calls() -> list[SignalCall]:
    with get_session() as session:
        rows = session.execute(
            select(SignalCall)
            .where(SignalCall.entry_date >= VALIDATION_PROTOCOL_START_DATE)
            .where(SignalCall.signal_name.in_(list(VALIDATION_SIGNAL_SPECS)))
            .order_by(SignalCall.entry_date, SignalCall.symbol)
        ).scalars().all()
        session.expunge_all()
    return rows


def build_validation_status() -> dict[str, Any]:
    calls = _load_calls()
    by_signal: dict[str, list[SignalCall]] = defaultdict(list)
    for call in calls:
        by_signal[call.signal_name].append(call)

    results: dict[str, Any] = {}
    all_passed = True
    any_failed = False
    all_ready = True

    for signal_name, spec in VALIDATION_SIGNAL_SPECS.items():
        scoped = [c for c in by_signal.get(signal_name, []) if c.forward_days_horizon == spec.horizon_trading_days]
        resolved = [
            c
            for c in scoped
            if c.status == SignalCallStatus.RESOLVED
            and c.resolution_price is not None
            and c.entry_price is not None
        ]
        pending_count = sum(c.status == SignalCallStatus.PENDING for c in scoped)
        void_count = sum(c.status == SignalCallStatus.VOID for c in scoped)

        net_returns_by_day: dict[Any, list[float]] = defaultdict(list)
        after_fee_wins_by_day: dict[Any, list[float]] = defaultdict(list)
        logic_hashes: set[str] = set()

        for call in resolved:
            entry_price = float(call.entry_price)
            resolution_price = float(call.resolution_price)
            gross_return_percent = (resolution_price / entry_price - 1.0) * 100.0
            net_return_percent = gross_return_percent - cost_percent()
            net_returns_by_day[call.entry_date].append(net_return_percent)
            after_fee_wins_by_day[call.entry_date].append(1.0 if net_return_percent > 0 else 0.0)
            logic_hashes.add(call.signal_logic_commit_hash)

        daily_net_returns = [mean(values) for values in net_returns_by_day.values()]
        daily_win_rates = [mean(values) for values in after_fee_wins_by_day.values()]
        mean_net, net_ci_low, net_ci_high = _mean_ci_95(daily_net_returns)
        mean_win, win_ci_low, win_ci_high = _mean_ci_95(daily_win_rates)

        logic_consistent = len(logic_hashes) <= 1
        sample_ready = len(resolved) >= spec.min_graded_calls
        cluster_ready = len(daily_net_returns) >= MIN_INDEPENDENT_ENTRY_DAYS
        ready = logic_consistent and sample_ready and cluster_ready

        if not logic_consistent:
            gate_status = "invalid_logic_change"
        elif not ready:
            gate_status = "collecting"
        else:
            passed = bool(
                net_ci_low is not None
                and win_ci_low is not None
                and net_ci_low > 0.0
                and win_ci_low > 0.5
            )
            gate_status = "pass" if passed else "fail"

        if gate_status != "pass":
            all_passed = False
        if gate_status == "fail" or gate_status == "invalid_logic_change":
            any_failed = True
        if gate_status == "collecting":
            all_ready = False

        results[signal_name] = {
            "horizon_trading_days": spec.horizon_trading_days,
            "required_liquidity_tier": spec.required_liquidity_tier,
            "min_graded_calls": spec.min_graded_calls,
            "min_independent_entry_days": MIN_INDEPENDENT_ENTRY_DAYS,
            "graded_calls": len(resolved),
            "independent_entry_days": len(daily_net_returns),
            "pending_calls": pending_count,
            "void_calls": void_count,
            "logic_hashes": sorted(logic_hashes),
            "mean_clustered_net_return_percent": mean_net,
            "net_return_ci_95_low": net_ci_low,
            "net_return_ci_95_high": net_ci_high,
            "mean_clustered_after_fee_win_rate": mean_win,
            "win_rate_ci_95_low": win_ci_low,
            "win_rate_ci_95_high": win_ci_high,
            "gate_status": gate_status,
        }

    if all_passed and all_ready:
        overall_status = "pass"
    elif any_failed:
        overall_status = "fail"
    else:
        overall_status = "collecting"

    return {
        "policy_version": VALIDATION_POLICY_VERSION,
        "protocol_start_date": VALIDATION_PROTOCOL_START_DATE.isoformat(),
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        "cluster_unit": "entry_date",
        "confidence_level": 0.95,
        "pass_rule": (
            "For every signal: minimum calls and entry-date clusters reached, one stable signal-logic hash, "
            "lower 95% CI of clustered mean net return > 0%, and lower 95% CI of clustered after-fee win rate > 50%."
        ),
        "overall_status": overall_status,
        "signals": results,
    }


def _print_human(status_payload: dict[str, Any]) -> None:
    print(
        f"ArthaSignal validation {status_payload['policy_version']} | "
        f"overall={status_payload['overall_status']} | cost={status_payload['round_trip_cost_bps']} bps"
    )
    print("-" * 120)
    for signal_name, row in status_payload["signals"].items():
        net = row["mean_clustered_net_return_percent"]
        win = row["mean_clustered_after_fee_win_rate"]
        net_text = "n/a" if net is None else f"{net:+.3f}%"
        win_text = "n/a" if win is None else f"{win * 100:.1f}%"
        print(
            f"{signal_name:30} {row['gate_status']:20} "
            f"calls={row['graded_calls']:3}/{row['min_graded_calls']:3} "
            f"days={row['independent_entry_days']:2}/{row['min_independent_entry_days']:2} "
            f"net={net_text:>9} win={win_text:>7} void={row['void_calls']} pending={row['pending_calls']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Show forward paper-trade validation status")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()

    payload = build_validation_status()
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        _print_human(payload)


if __name__ == "__main__":
    main()
