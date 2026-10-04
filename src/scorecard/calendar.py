from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

NPT = ZoneInfo("Asia/Kathmandu")
CONFIG_PATH = Path(os.environ.get("ARTHASIGNAL_CALENDAR", Path(__file__).resolve().parents[2] / "config" / "nepse_calendar.json"))
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
SEARCH_DAYS = 45


@dataclass(frozen=True)
class Calendar:
    rules: tuple[tuple[date, frozenset[int]], ...]
    holidays: frozenset[date]
    market_open: time

    def weekdays_on(self, day: date) -> frozenset[int]:
        current = frozenset()
        for start, weekdays in self.rules:
            if start <= day:
                current = weekdays
        return current

    def rule_session(self, day: date) -> bool:
        return day.weekday() in self.weekdays_on(day) and day not in self.holidays

    def next_session(self, after: date, known_sessions: Iterable[date] = ()) -> date:
        known = sorted(d for d in known_sessions if d > after)
        day = after + timedelta(days=1)
        for _ in range(SEARCH_DAYS):
            if (known and known[0] == day) or self.rule_session(day):
                return day
            day += timedelta(days=1)
        raise LookupError(f"no NEPSE session within {SEARCH_DAYS} days after {after}")

    def next_open(self, after: date, known_sessions: Iterable[date] = ()) -> datetime:
        return datetime.combine(self.next_session(after, known_sessions), self.market_open, tzinfo=NPT)


def parse(config: dict) -> Calendar:
    rules = tuple(sorted(
        (date.fromisoformat(r["effective_from"]), frozenset(DAY_NAMES.index(d) for d in r["weekdays"])) for r in config["weekday_rules"]
    ))
    holidays = frozenset(date.fromisoformat(d if isinstance(d, str) else d["date"]) for d in config.get("holidays", []))
    hour, minute = (int(x) for x in config.get("market_open", "11:00").split(":"))
    return Calendar(rules, holidays, time(hour, minute))


@lru_cache(maxsize=1)
def load(path: str = str(CONFIG_PATH)) -> Calendar:
    return parse(json.loads(Path(path).read_text()))


def signal_date_for(moment: datetime, sessions: Sequence[date], calendar: Calendar | None = None) -> date | None:
    calendar = calendar or load()
    moment = moment.astimezone(NPT)
    opened = [s for s in sessions if datetime.combine(s, calendar.market_open, tzinfo=NPT) <= moment]
    if not opened:
        return None
    latest = opened[-1]
    return latest if calendar.next_open(latest, sessions) > moment else None
