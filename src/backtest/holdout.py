from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from functools import wraps
from typing import Any, Callable, Collection, Iterable, Sequence

from src.backtest.config import HoldoutConfig
from src.backtest.ledger import STATUS_COMPLETED, STATUS_FAILED, BacktestLedger
from src.backtest.report import selection_report
from src.backtest.types import Row

DEVELOPMENT_PURPOSES: tuple[str, ...] = ("training", "feature_selection", "tuning", "walk_forward")
_UNSEAL = object()


class HoldoutViolation(Exception):
    pass


def touches_holdout(row: Row, config: HoldoutConfig) -> bool:
    if row.signal_date >= config.holdout_start:
        return True
    return row.label is not None and row.label.exit_date >= config.holdout_start


def assert_development_only(rows: Iterable[Row], config: HoldoutConfig, purpose: str) -> None:
    if purpose not in DEVELOPMENT_PURPOSES:
        raise ValueError(f"unknown development purpose: {purpose}")
    offending = [row for row in rows if row.label is not None and touches_holdout(row, config)]
    if offending:
        first = min(row.signal_date for row in offending)
        raise HoldoutViolation(
            f"{purpose} received {len(offending)} labeled rows inside the locked holdout "
            f"(holdout starts {config.holdout_start}, earliest offending signal date {first}); "
            "holdout labels may only be read by final_evaluation()"
        )


def development_only(purpose: str, config: HoldoutConfig) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(function: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(function)
        def wrapper(rows: Sequence[Row], *args: Any, **kwargs: Any) -> Any:
            assert_development_only(rows, config, purpose)
            return function(rows, *args, **kwargs)

        return wrapper

    return decorator


class SealedHoldout:
    __slots__ = ("__rows", "config")

    def __init__(self, rows: Sequence[Row], config: HoldoutConfig) -> None:
        self.__rows = tuple(rows)
        self.config = config

    @property
    def count(self) -> int:
        return len(self.__rows)

    def feature_rows(self) -> tuple[Row, ...]:
        return tuple(replace(row, label=None, benchmark_return=None) for row in self.__rows)

    def labeled_rows(self, token: object = None) -> tuple[Row, ...]:
        if token is not _UNSEAL:
            raise HoldoutViolation("holdout labels may only be read by final_evaluation()")
        return self.__rows

    def __iter__(self) -> Any:
        raise HoldoutViolation("the sealed holdout cannot be iterated; use feature_rows() or final_evaluation()")

    def __len__(self) -> int:
        raise HoldoutViolation("the sealed holdout has no public length; use count")

    def __getitem__(self, index: Any) -> Any:
        raise HoldoutViolation("the sealed holdout cannot be indexed; use feature_rows() or final_evaluation()")


@dataclass(frozen=True)
class Partition:
    development: tuple[Row, ...]
    holdout: SealedHoldout
    boundary_dropped: int


def partition_rows(rows: Iterable[Row], config: HoldoutConfig) -> Partition:
    development: list[Row] = []
    holdout: list[Row] = []
    boundary_dropped = 0
    for row in rows:
        if row.signal_date >= config.holdout_start:
            if config.holdout_end is None or row.signal_date <= config.holdout_end:
                holdout.append(row)
            continue
        if touches_holdout(row, config):
            boundary_dropped += 1
            continue
        development.append(row)
    return Partition(tuple(development), SealedHoldout(holdout, config), boundary_dropped)


def final_evaluation(
    *,
    model_id: str,
    select: Callable[[tuple[Row, ...]], Collection[tuple[str, date]]],
    holdout: SealedHoldout,
    ledger: BacktestLedger,
    requested_by: str,
    reason: str,
) -> dict[str, Any]:
    if not model_id.strip() or not requested_by.strip() or not reason.strip():
        raise ValueError("final_evaluation requires model_id, requested_by and reason")
    from src.database.holdout_guard import allow

    call_id, call_number = ledger.start_holdout_call(model_id, holdout.config, requested_by, reason)
    try:
        with allow("final_evaluation"):
            variants_tried = ledger.variant_count()
            selected = set(select(holdout.feature_rows()))
            report = selection_report(holdout.labeled_rows(_UNSEAL), selected, holdout.config, variants_tried)
    except Exception:
        ledger.finish_holdout_call(call_id, STATUS_FAILED, None)
        raise
    report["model_id"] = model_id
    report["holdout_call_number"] = call_number
    report["holdout_start"] = holdout.config.holdout_start.isoformat()
    ledger.finish_holdout_call(call_id, STATUS_COMPLETED, report)
    return report
