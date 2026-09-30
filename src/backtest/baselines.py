from __future__ import annotations

from typing import Sequence

from src.backtest.types import Row

BASELINE_NEPSE = "nepse_buy_and_hold"
BASELINE_EQUAL_WEIGHT = "equal_weight_universe"
BASELINE_MOMENTUM = "simple_momentum"
BASELINE_NAMES: tuple[str, ...] = (BASELINE_NEPSE, BASELINE_EQUAL_WEIGHT, BASELINE_MOMENTUM)


def nepse_buy_and_hold_returns(selected_rows: Sequence[Row]) -> list[float]:
    return [float(row.benchmark_return) for row in selected_rows if row.benchmark_return is not None]


def equal_weight_universe_returns(date_rows: Sequence[Row]) -> list[float]:
    return [float(row.label.gross_return) for row in date_rows if row.label is not None]


def simple_momentum_selection(date_rows: Sequence[Row], breadth: int) -> list[Row]:
    ranked = sorted(
        (row for row in date_rows if row.label is not None and row.momentum_score is not None),
        key=lambda row: (-float(row.momentum_score), row.symbol),
    )
    return ranked[:breadth]


def simple_momentum_returns(date_rows: Sequence[Row], breadth: int) -> list[float]:
    return [float(row.label.gross_return) for row in simple_momentum_selection(date_rows, breadth)]
