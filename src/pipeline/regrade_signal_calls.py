from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from datetime import date
from typing import Any

from sqlalchemy import select, update

from src.database.connection import get_session
from src.database.models import SignalCall, SignalCallStatus
from src.pipeline.grade_signal_calls import (
    _load_trading_days,
    _resolution_target_date,
    grade_signal_calls,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _status_counts() -> dict[str, int]:
    with get_session() as session:
        rows = session.execute(select(SignalCall.status, SignalCall.outcome)).all()
    counts: Counter[str] = Counter()
    for status, outcome in rows:
        label = status.value if outcome is None or outcome.value == status.value else f"{status.value}/{outcome.value}"
        counts[label] += 1
    return dict(sorted(counts.items()))


def _load_graded_calls() -> list[dict[str, Any]]:
    with get_session() as session:
        rows = session.execute(
            select(
                SignalCall.id,
                SignalCall.entry_date,
                SignalCall.forward_days_horizon,
                SignalCall.status,
                SignalCall.outcome,
                SignalCall.resolution_date,
            ).where(SignalCall.status != SignalCallStatus.PENDING)
        ).all()
    return [
        {
            "id": row.id,
            "entry_date": row.entry_date,
            "horizon": row.forward_days_horizon,
            "status": row.status,
            "outcome": row.outcome,
            "resolution_date": row.resolution_date,
        }
        for row in rows
    ]


def is_miscounted(call: dict[str, Any], sessions: list[date]) -> bool:
    if call["status"] == SignalCallStatus.VOID:
        return True
    target_date = _resolution_target_date(call["entry_date"], call["horizon"], sessions)
    if target_date is None or call["resolution_date"] is None:
        return True
    return call["resolution_date"] < target_date


def regrade_miscounted_signal_calls(as_of: date | None = None, dry_run: bool = False) -> dict[str, Any]:
    as_of = as_of or date.today()
    sessions = _load_trading_days()
    graded = _load_graded_calls()
    miscounted = [call for call in graded if is_miscounted(call, sessions)]
    before = _status_counts()
    previous_outcome = {call["id"]: call["outcome"].value if call["outcome"] else None for call in miscounted}

    summary: dict[str, Any] = {
        "as_of": as_of.isoformat(),
        "sessions_with_prices": len(sessions),
        "graded_calls_checked": len(graded),
        "miscounted_calls": len(miscounted),
        "status_before": before,
        "dry_run": dry_run,
    }
    if dry_run or not miscounted:
        logger.info("Signal call regrade summary: %s", summary)
        return summary

    ids = [call["id"] for call in miscounted]
    with get_session() as session:
        session.execute(
            update(SignalCall)
            .where(SignalCall.id.in_(ids))
            .values(
                status=SignalCallStatus.PENDING,
                outcome=None,
                resolution_date=None,
                resolution_price=None,
            )
        )

    grading_summary = grade_signal_calls(as_of)

    with get_session() as session:
        rows = session.execute(
            select(SignalCall.id, SignalCall.status, SignalCall.outcome).where(SignalCall.id.in_(ids))
        ).all()
    transitions: Counter[str] = Counter()
    for call_id, status, outcome in rows:
        new_label = outcome.value if outcome is not None else status.value
        transitions[f"{previous_outcome[call_id]} -> {new_label}"] += 1

    summary["grading"] = grading_summary
    summary["transitions"] = dict(sorted(transitions.items()))
    summary["status_after"] = _status_counts()
    logger.info("Signal call regrade summary: %s", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-grade signal calls whose horizon was counted on non-session days")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(regrade_miscounted_signal_calls(dry_run=args.dry_run), indent=2, default=str))


if __name__ == "__main__":
    main()
