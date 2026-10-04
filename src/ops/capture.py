from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import text

from src.scorecard.calendar import NPT, load

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAILED = 2
EXIT_NO_SESSION = 3
MIN_PRICE_ROWS = 100
RETRY_MINUTES = 15
DEFAULT_CUTOFF = "20:30"


def session_rows(day: date) -> dict[str, int]:
    from src.database.connection import engine

    with engine.connect() as connection:
        prices = connection.execute(text("SELECT count(*) FROM daily_prices WHERE date = :d"), {"d": day}).scalar_one()
        index = connection.execute(
            text("SELECT count(*) FROM market_index WHERE date = :d AND index_name = 'NEPSE Index'"), {"d": day}
        ).scalar_one()
    return {"price_rows": int(prices), "nepse_index_rows": int(index)}


def attempt(day: date) -> dict[str, Any]:
    from src.pipeline.backfill_calendar import run_calendar_backfill
    from src.pipeline.backfill_daily_index import run_daily_index_refresh
    from src.pipeline.run_daily import run_daily_pipeline
    from src.pipeline.sync_market_data import repair_market_gaps, should_auto_repair_gaps

    out: dict[str, Any] = {}
    for name, fn in (
        ("gap_repair", lambda: repair_market_gaps() if should_auto_repair_gaps() else {"skipped": "no gap"}),
        ("prices", run_daily_pipeline),
        ("index", lambda: run_daily_index_refresh(today=day)),
        ("calendar", lambda: run_calendar_backfill(attempt_confirmed_for_today=True)),
    ):
        try:
            out[name] = fn()
        except Exception as error:
            logger.exception("capture step %s failed", name)
            out[name] = {"error": repr(error)}
    return out


def classify(rows: dict[str, int], expected: bool) -> str:
    if rows["price_rows"] >= MIN_PRICE_ROWS and rows["nepse_index_rows"] >= 1:
        return "session"
    if rows["price_rows"] == 0 and not expected:
        return "no_session"
    return "incomplete"


def run(day: date, cutoff: datetime, sleep: Any = time.sleep) -> tuple[int, dict[str, Any]]:
    from src.pipeline.backfill_calendar import is_trading_day

    calendar_says = load().rule_session(day)
    learned_says = is_trading_day(day)
    report: dict[str, Any] = {"date": day.isoformat(), "calendar_expects_session": calendar_says, "learned_calendar_expects_session": learned_says,
                              "attempts": []}
    if calendar_says != learned_says:
        report["warning"] = "config calendar and learned trading calendar disagree; check NEPSE notices and config/nepse_calendar.json"
    expected = calendar_says or learned_says
    while True:
        result = attempt(day)
        rows = session_rows(day)
        state = classify(rows, expected)
        report["attempts"].append({"at": datetime.now(tz=NPT).isoformat(), "rows": rows, "state": state,
                                   "errors": {k: v["error"] for k, v in result.items() if isinstance(v, dict) and "error" in v}})
        if state == "session":
            report["result"] = "session captured"
            return EXIT_OK, report
        if state == "no_session":
            report["result"] = "no session today"
            return EXIT_NO_SESSION, report
        if datetime.now(tz=NPT) + timedelta(minutes=RETRY_MINUTES) > cutoff:
            report["result"] = ("no complete price and index data by the cutoff; either an unlisted holiday or a capture failure. "
                                "If NEPSE was closed, add the date to config/nepse_calendar.json holidays")
            return EXIT_FAILED, report
        sleep(RETRY_MINUTES * 60)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Capture today's prices and indices with source fallbacks and retries")
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    args = parser.parse_args()
    day = args.date or datetime.now(tz=NPT).date()
    hour, minute = (int(x) for x in args.cutoff.split(":"))
    cutoff = datetime.combine(datetime.now(tz=NPT).date(), datetime.min.time(), tzinfo=NPT).replace(hour=hour, minute=minute)
    code, report = run(day, cutoff)
    print(json.dumps(report, indent=2, default=str))
    sys.exit(code)


if __name__ == "__main__":
    main()
