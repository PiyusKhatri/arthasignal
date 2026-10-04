from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "simulation_protocol.yaml"


def as_date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def effective_row(schedule: Sequence[Mapping[str, Any]], day: date) -> Mapping[str, Any]:
    chosen = None
    for row in sorted(schedule, key=lambda r: as_date(r["effective_from"])):
        if as_date(row["effective_from"]) <= day:
            chosen = row
    if chosen is None:
        raise ValueError(f"no schedule row is in force on {day}")
    return chosen


@dataclass(frozen=True)
class Protocol:
    raw: Mapping[str, Any]
    sha256: str

    @property
    def version(self) -> str:
        return str(self.raw["protocol"]["version"])

    @property
    def real_open_start(self) -> date:
        return as_date(self.raw["calendar"]["real_open_start"])

    @property
    def settlement_sessions(self) -> int:
        return int(self.raw["entry"]["settlement_sessions_before_sell"])

    @property
    def lock_tolerance(self) -> float:
        return float(self.raw["circuit"]["lock_tolerance"])

    def circuit_limit(self, day: date) -> float:
        return float(effective_row(self.raw["circuit"]["limits"], day)["limit"])

    def holding_range(self, horizon_class: str) -> tuple[int, int]:
        row = self.raw["horizons"]["classes"][horizon_class]
        return int(row["min_sessions"]), int(row["max_sessions"])

    def call_for_score(self, score: float) -> str:
        bounds = self.raw["score"]
        if not bounds["min"] <= score <= bounds["max"]:
            raise ValueError(f"score {score} is outside {bounds['min']}..{bounds['max']}")
        rounded = int(Decimal(str(score)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        for band in bounds["bands"]:
            if band["min"] <= rounded <= band["max"]:
                return str(band["call"])
        raise ValueError(f"score {score} falls in no band")


@lru_cache(maxsize=None)
def load(path: str | None = None) -> Protocol:
    data = Path(path or CONFIG_PATH).read_bytes()
    return Protocol(yaml.safe_load(data), hashlib.sha256(data).hexdigest())
