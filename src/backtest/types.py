from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping

EXIT_ON_TIME = "on_time"
EXIT_DELAYED = "delayed"
EXIT_BLOCKED = "blocked"
EXIT_STRANDED = "stranded"


@dataclass(frozen=True)
class Bar:
    open: float | None
    close: float | None


@dataclass(frozen=True)
class Label:
    entry_date: date
    entry_price: float
    exit_date: date
    exit_price: float
    gross_return: float
    exit_status: str
    blocked_sessions: int = 0


@dataclass(frozen=True)
class Row:
    symbol: str
    signal_date: date
    features: Mapping[str, Any] = field(default_factory=dict)
    label: Label | None = None
    benchmark_return: float | None = None
    momentum_score: float | None = None

    @property
    def key(self) -> tuple[str, date]:
        return (self.symbol, self.signal_date)
