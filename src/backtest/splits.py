from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Sequence

from src.backtest.config import HoldoutConfig
from src.backtest.holdout import assert_development_only
from src.backtest.types import Row

DEFAULT_FOLDS = 4
DEFAULT_MIN_TRAIN_SESSIONS = 252


@dataclass(frozen=True)
class Fold:
    index: int
    train_sessions: tuple[date, ...]
    test_sessions: tuple[date, ...]
    gap_sessions: int


def walk_forward_folds(
    sessions: Iterable[date],
    config: HoldoutConfig,
    n_folds: int = DEFAULT_FOLDS,
    min_train_sessions: int = DEFAULT_MIN_TRAIN_SESSIONS,
) -> list[Fold]:
    development = sorted({session for session in sessions if session < config.holdout_start})
    gap = config.purge_sessions + config.embargo_sessions
    available = len(development) - min_train_sessions - gap
    if n_folds < 1 or available < n_folds:
        raise ValueError(
            f"not enough development sessions for {n_folds} folds: "
            f"{len(development)} sessions, {min_train_sessions} minimum training, {gap} gap"
        )
    test_size = available // n_folds
    folds: list[Fold] = []
    for position in range(n_folds):
        test_start = min_train_sessions + gap + position * test_size
        test_end = test_start + test_size if position < n_folds - 1 else len(development)
        folds.append(
            Fold(
                index=position + 1,
                train_sessions=tuple(development[: test_start - gap]),
                test_sessions=tuple(development[test_start:test_end]),
                gap_sessions=gap,
            )
        )
    return folds


def fold_rows(rows: Sequence[Row], fold: Fold, config: HoldoutConfig) -> tuple[list[Row], list[Row]]:
    assert_development_only(rows, config, "walk_forward")
    train_sessions = set(fold.train_sessions)
    test_sessions = set(fold.test_sessions)
    test_start = fold.test_sessions[0]
    train = [
        row
        for row in rows
        if row.signal_date in train_sessions and row.label is not None and row.label.exit_date < test_start
    ]
    test = [row for row in rows if row.signal_date in test_sessions]
    return train, test
