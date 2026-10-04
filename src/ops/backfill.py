from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import text

from src.scorecard.calendar import NPT, load

logger = logging.getLogger(__name__)


def missing_sessions(since: date, until: date) -> dict[str, Any]:
    from src.database.connection import engine

    with engine.connect() as connection:
        present = {r[0] for r in connection.execute(
            text("SELECT DISTINCT date FROM daily_prices WHERE date > :s AND date <= :u"), {"s": since, "u": until})}
        index = {r[0] for r in connection.execute(
            text("SELECT DISTINCT date FROM market_index WHERE index_name = 'NEPSE Index' AND date > :s AND date <= :u"), {"s": since, "u": until})}
    calendar = load()
    expected, day = [], since + timedelta(days=1)
    while day <= until:
        if calendar.rule_session(day) or day in present:
            expected.append(day)
        day += timedelta(days=1)
    return {"expected_by_calendar": [d.isoformat() for d in expected],
            "missing_prices": [d.isoformat() for d in expected if d not in present],
            "missing_index": [d.isoformat() for d in expected if d not in index]}


def backfill(since: date, until: date) -> dict[str, Any]:
    from src.pipeline.backfill_calendar import run_calendar_backfill
    from src.pipeline.sync_market_data import repair_index_gaps, repair_price_gaps

    before = missing_sessions(since, until)
    out: dict[str, Any] = {"before": before}
    out["prices"] = repair_price_gaps(since + timedelta(days=1), until)
    out["indices"] = repair_index_gaps(since + timedelta(days=1), until)
    out["calendar"] = run_calendar_backfill(attempt_confirmed_for_today=False)
    after = missing_sessions(since, until)
    out["after"] = after
    out["note"] = ("days still missing are either holidays not in config/nepse_calendar.json or sessions no source could supply; "
                   "check NEPSE notices before adding a holiday")
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Backfill missing price and index sessions after a date")
    parser.add_argument("--since", type=date.fromisoformat, required=True)
    parser.add_argument("--until", type=date.fromisoformat)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    until = args.until or datetime.now(tz=NPT).date()
    report = missing_sessions(args.since, until) if args.dry_run else backfill(args.since, until)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
