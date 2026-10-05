from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date
from typing import Any, Callable, Iterable

from sqlalchemy import text

from src.database.holdout_guard import HOLDOUT_START
from src.scorecard import spec

logger = logging.getLogger(__name__)

DEFAULT_DELAY = 2.0
EXIT_ALL_FAILED = 2


def active_symbols() -> list[str]:
    from src.database.connection import engine

    with engine.connect() as connection:
        rows = connection.execute(text("SELECT symbol FROM companies WHERE instrument_type = 'Equity' AND status = 'A' ORDER BY symbol")).all()
    return [r[0] for r in rows if spec.valid_symbol(r[0])]


def refresh(
    symbols: Iterable[str],
    fetch: Callable[[str], list[dict[str, Any]]],
    insert: Callable[[list[dict[str, Any]]], tuple[int, int]] | None,
    delay: float = DEFAULT_DELAY,
    sleep: Callable[[float], Any] = time.sleep,
    live_from: date = HOLDOUT_START,
) -> dict[str, Any]:
    symbols = list(symbols)
    report: dict[str, Any] = {"symbols": len(symbols), "live_from": live_from.isoformat(), "dry_run": insert is None,
                              "rows_fetched": 0, "live_rows": 0, "older_rows_not_inserted": 0, "inserted": 0,
                              "duplicates": 0, "errors": {}, "live_actions": []}
    for k, symbol in enumerate(symbols):
        if k:
            sleep(delay)
        try:
            rows = fetch(symbol)
        except Exception as error:
            report["errors"][symbol] = type(error).__name__
            logger.warning("%s: %s", symbol, error)
            continue
        live = [r for r in rows if r["action_date"] >= live_from]
        report["rows_fetched"] += len(rows)
        report["live_rows"] += len(live)
        report["older_rows_not_inserted"] += len(rows) - len(live)
        report["live_actions"] += [{"symbol": r["symbol"], "action_date": r["action_date"].isoformat(), "type": r["action_type"],
                                    "value": float(r["ratio_or_amount"])} for r in live]
        if live and insert is not None:
            inserted, duplicates = insert(live)
            report["inserted"] += inserted
            report["duplicates"] += duplicates
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Record bonus, dividend and right book closes dated from the holdout start (insert-only)")
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.delay < 2.0:
        raise SystemExit("--delay below 2 seconds is not allowed")
    from src.pipeline.db_writers import insert_new_corporate_actions
    from src.scrapers.corporate_actions_scraper import get_corporate_actions

    symbols = args.symbols or active_symbols()
    report = refresh(symbols, get_corporate_actions, None if args.dry_run else insert_new_corporate_actions, args.delay)
    print(json.dumps(report, indent=2, default=str))
    if symbols and len(report["errors"]) == len(symbols):
        sys.exit(EXIT_ALL_FAILED)


if __name__ == "__main__":
    main()
