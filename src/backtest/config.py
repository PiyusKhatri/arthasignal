from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("holdout_config.json")


@dataclass(frozen=True)
class HoldoutConfig:
    version: str
    locked_on: date
    holdout_start: date
    holdout_end: date | None
    horizon_sessions: int
    grace_sessions: int
    embargo_sessions: int
    cost_levels_round_trip: tuple[float, ...]
    base_alpha: float
    sha256: str

    @property
    def purge_sessions(self) -> int:
        return self.horizon_sessions + self.grace_sessions + 1


def load_holdout_config(path: Path = CONFIG_PATH) -> HoldoutConfig:
    raw = path.read_bytes()
    data = json.loads(raw)
    holdout_end = data.get("holdout_end")
    return HoldoutConfig(
        version=str(data["version"]),
        locked_on=date.fromisoformat(data["locked_on"]),
        holdout_start=date.fromisoformat(data["holdout_start"]),
        holdout_end=date.fromisoformat(holdout_end) if holdout_end else None,
        horizon_sessions=int(data["horizon_sessions"]),
        grace_sessions=int(data["grace_sessions"]),
        embargo_sessions=int(data["embargo_sessions"]),
        cost_levels_round_trip=tuple(float(level) for level in data["cost_levels_round_trip"]),
        base_alpha=float(data["base_alpha"]),
        sha256=hashlib.sha256(raw).hexdigest(),
    )
