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


def round_score(score: float) -> int:
    return int(Decimal(str(score)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


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

    def settlement_sessions(self, entry_day: date) -> int:
        return int(effective_row(self.raw["entry"]["settlement"]["schedules"], entry_day)["sessions_before_sell"])

    @property
    def holdout_start(self) -> date:
        return as_date(self.raw["protocol"]["holdout_start"])

    @property
    def stop_required(self) -> tuple[str, ...]:
        return tuple(self.raw["target_stop"]["stop_required"])

    def floorsheet_fields(self) -> tuple[str, ...]:
        use = self.raw["floorsheet_ohlc"]["use"]
        return tuple(field for field in ("open", "high", "low") if use.get(field))

    def periods(self) -> list[tuple[str, date, date]]:
        raw = self.raw["periods"]
        rows = [("learning", raw["learning"]), ("check", raw["check"])] + [(row["name"], row) for row in raw["exam"]]
        return [(name, as_date(row["start"]), as_date(row["end"])) for name, row in rows]

    def exam_periods(self) -> list[tuple[str, date, date]]:
        return [(str(row["name"]), as_date(row["start"]), as_date(row["end"])) for row in self.raw["periods"]["exam"]]

    def period_of(self, day: date) -> str | None:
        for name, start, end in self.periods():
            if start <= day <= end:
                return name
        return None

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
        rounded = round_score(score)
        for band in bounds["bands"]:
            if band["min"] <= rounded <= band["max"]:
                return str(band["call"])
        raise ValueError(f"score {score} falls in no band")


@lru_cache(maxsize=None)
def load(path: str | None = None) -> Protocol:
    data = Path(path or CONFIG_PATH).read_bytes()
    return Protocol(yaml.safe_load(data), hashlib.sha256(data).hexdigest())
