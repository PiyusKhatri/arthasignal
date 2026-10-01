from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import Company
from src.pipeline.backfill_price_session import insert_price_rows_only_new, is_valid_price_row
from src.scrapers import sharesansar_scraper

logger = logging.getLogger(__name__)

DEFAULT_START = date(2014, 6, 1)
DEFAULT_END = date(2021, 7, 24)
DEFAULT_STATE_PATH = Path("logs/price_history_backfill_state.jsonl")
DEFAULT_FLOORSHEET_ROOT = Path(os.path.expanduser("~/Desktop/arthasignal-ai/raw/floorsheet"))
CHUNK_DAYS = 30
MAX_ATTEMPTS = 4
SATURDAY = 5
DELISTED_STATUS = "D"

DEBENTURE_SYMBOL = re.compile(r"^[A-Z]+D\d{2,4}(/\d{2})?$|^[A-Z]+\d{2}/\d{2}$")


def infer_instrument_type(symbol: str, name: str | None) -> str:
    text = (name or "").lower()
    if "promoter" in text or symbol.endswith("PO"):
        return "Promoter Shares"
    if "pref" in text:
        return "Preference Shares"
    if any(word in text for word in ("debenture", "bond", "rinpatra")) or DEBENTURE_SYMBOL.match(symbol):
        return "Non-Convertible Debentures"
    if any(word in text for word in ("fund", "scheme", "yojana", "kosh")):
        return "Mutual Funds"
    return "Equity"


def load_floorsheet_names(root: Path) -> dict[str, str]:
    import pyarrow.parquet as pq

    names: dict[str, str] = {}
    for path in sorted(glob.glob(str(root / "year=*" / "month=*" / "day=*.parquet"))):
        table = pq.read_table(path, columns=["symbol", "security_name"])
        for symbol, name in zip(table.column("symbol").to_pylist(), table.column("security_name").to_pylist()):
            if symbol and name:
                names[symbol] = name
    return names


def completed_dates(state_path: Path) -> set[date]:
    if not state_path.exists():
        return set()
    done: set[date] = set()
    for line in state_path.read_text().splitlines():
        record = json.loads(line)
        if record.get("status") in {"inserted", "no_session"}:
            done.add(date.fromisoformat(record["date"]))
    return done


def candidate_dates(start: date, end: date) -> list[date]:
    days = []
    current = start
    while current <= end:
        if current.weekday() != SATURDAY:
            days.append(current)
        current += timedelta(days=1)
    return days


def _known_symbols() -> set[str]:
    with get_session() as session:
        return set(session.execute(select(Company.symbol)).scalars().all())


def ensure_companies(symbols: set[str], names: dict[str, str]) -> list[dict[str, Any]]:
    missing = sorted(symbols - _known_symbols())
    if not missing:
        return []
    records = [
        {
            "symbol": symbol,
            "company_name": names.get(symbol, symbol)[:255],
            "sector": None,
            "instrument_type": infer_instrument_type(symbol, names.get(symbol)),
            "status": DELISTED_STATUS,
        }
        for symbol in missing
    ]
    stmt = pg_insert(Company).values(records).on_conflict_do_nothing(index_elements=["symbol"])
    with get_session() as session:
        session.execute(stmt)
    return records


def dedupe_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    seen: set[str] = set()
    unique = []
    for row in rows:
        if row["symbol"] in seen:
            continue
        seen.add(row["symbol"])
        unique.append(row)
    return unique, len(rows) - len(unique)


def _fetch_with_retry(day: date, client: dict[str, Any]) -> tuple[date | None, list[dict[str, Any]]]:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            if client.get("http") is None:
                client["http"], client["token"] = sharesansar_scraper.open_price_session()
            return sharesansar_scraper.get_session_prices(day, http=client["http"], token=client["token"])
        except Exception as exc:
            logger.warning("%s attempt %d failed: %s", day, attempt, exc)
            client["http"] = None
            time.sleep(5 * attempt)
    raise RuntimeError(f"giving up on {day} after {MAX_ATTEMPTS} attempts")


def backfill_day(day: date, client: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    as_of, rows = _fetch_with_retry(day, client)
    record: dict[str, Any] = {"date": day.isoformat(), "source_as_of": as_of.isoformat() if as_of else None}
    if as_of != day:
        record.update(status="date_mismatch", source_rows=len(rows))
        return record
    if not rows:
        record.update(status="no_session", source_rows=0)
        return record

    valid = [row for row in rows if is_valid_price_row(row)]
    unique, duplicates = dedupe_rows(valid)
    created = ensure_companies({row["symbol"] for row in unique}, names)
    inserted = insert_price_rows_only_new(unique)
    record.update(
        status="inserted",
        source_rows=len(rows),
        invalid=len(rows) - len(valid),
        duplicates=duplicates,
        inserted=inserted,
        existing_kept=len(unique) - inserted,
        new_companies=[(c["symbol"], c["instrument_type"]) for c in created],
    )
    return record


def run_history_backfill(
    start: date,
    end: date,
    state_path: Path = DEFAULT_STATE_PATH,
    floorsheet_root: Path = DEFAULT_FLOORSHEET_ROOT,
) -> dict[str, Any]:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    done = completed_dates(state_path)
    todo = [day for day in candidate_dates(start, end) if day not in done]
    logger.info("History backfill %s to %s: %d dates done, %d to do", start, end, len(done), len(todo))

    names = load_floorsheet_names(floorsheet_root) if floorsheet_root.exists() else {}
    logger.info("Loaded %d symbol names from the floorsheet", len(names))

    totals = {"sessions": 0, "no_session": 0, "date_mismatch": 0, "failed": 0, "inserted": 0, "new_companies": 0}
    client: dict[str, Any] = {"http": None}
    started = time.perf_counter()

    for chunk_start in range(0, len(todo), CHUNK_DAYS):
        chunk = todo[chunk_start : chunk_start + CHUNK_DAYS]
        client["http"] = None
        for day in chunk:
            try:
                record = backfill_day(day, client, names)
            except Exception as exc:
                logger.exception("Failed %s", day)
                record = {"date": day.isoformat(), "status": "failed", "error": str(exc)}
            record["logged_at"] = datetime.now().isoformat(timespec="seconds")
            with state_path.open("a") as handle:
                handle.write(json.dumps(record) + "\n")

            status = record["status"]
            if status == "inserted":
                totals["sessions"] += 1
                totals["inserted"] += record["inserted"]
                totals["new_companies"] += len(record["new_companies"])
            else:
                totals[status] = totals.get(status, 0) + 1

        logger.info(
            "Progress: %d/%d dates, through %s, %s, %.0fs elapsed",
            min(chunk_start + CHUNK_DAYS, len(todo)),
            len(todo),
            chunk[-1],
            totals,
            time.perf_counter() - started,
        )

    logger.info("History backfill finished: %s", totals)
    return totals


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("src.scrapers.http_utils").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description="Backfill daily prices date by date from the public daily price page")
    parser.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START)
    parser.add_argument("--end", type=date.fromisoformat, default=DEFAULT_END)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--floorsheet-root", type=Path, default=DEFAULT_FLOORSHEET_ROOT)
    args = parser.parse_args()
    print(json.dumps(run_history_backfill(args.start, args.end, args.state, args.floorsheet_root), default=str))


if __name__ == "__main__":
    main()
