from __future__ import annotations

import argparse
import glob
import json
import logging
import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select

from src.database.connection import get_session
from src.database.models import DailyPrice
from src.pipeline.backfill_price_history import DEFAULT_FLOORSHEET_ROOT
from src.scrapers import sharesansar_scraper

logger = logging.getLogger(__name__)

PRICE_TOLERANCE = 0.011
PARTITION_DATE = re.compile(r"year=(\d{4})/month=(\d{2})/day=(\d{2})\.parquet$")
PRICE_FIELDS = ("open", "high", "low", "close")


def compare_price_rows(local: dict[str, Any], source: dict[str, Any]) -> list[str]:
    differing = [
        field for field in PRICE_FIELDS if abs(float(local[field]) - float(source[field] or 0)) > PRICE_TOLERANCE
    ]
    if int(local["volume"]) != int(source["volume"]):
        differing.append("volume")
    return differing


def _local_rows(day: date) -> dict[str, dict[str, Any]]:
    with get_session() as session:
        rows = session.execute(
            select(
                DailyPrice.symbol,
                DailyPrice.open,
                DailyPrice.high,
                DailyPrice.low,
                DailyPrice.close,
                DailyPrice.volume,
            ).where(DailyPrice.date == day)
        ).all()
    return {row.symbol: row._asdict() for row in rows}


def _sessions_between(start: date, end: date) -> list[date]:
    with get_session() as session:
        return list(
            session.execute(
                select(DailyPrice.date).distinct().where(DailyPrice.date.between(start, end)).order_by(DailyPrice.date)
            ).scalars()
        )


def validate_overlap(start: date, end: date, every: int) -> dict[str, Any]:
    sessions = _sessions_between(start, end)[::every]
    http, token = sharesansar_scraper.open_price_session()
    totals: Counter[str] = Counter()
    field_counts: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []
    for day in sessions:
        as_of, rows = sharesansar_scraper.get_session_prices(day, http=http, token=token)
        if as_of != day:
            totals["date_mismatch"] += 1
            continue
        source = {row["symbol"]: row for row in rows}
        local = _local_rows(day)
        common = set(source) & set(local)
        totals["sessions"] += 1
        totals["common_rows"] += len(common)
        totals["only_source"] += len(set(source) - set(local))
        totals["only_local"] += len(set(local) - set(source))
        for symbol in common:
            differing = compare_price_rows(local[symbol], source[symbol])
            if differing:
                totals["mismatched_rows"] += 1
                field_counts.update(differing)
                if len(examples) < 15:
                    examples.append(
                        {
                            "date": day.isoformat(),
                            "symbol": symbol,
                            "fields": differing,
                            "local": {f: float(local[symbol][f]) for f in (*PRICE_FIELDS, "volume")},
                            "source": {f: source[symbol][f] for f in (*PRICE_FIELDS, "volume")},
                        }
                    )
    common_rows = totals["common_rows"] or 1
    return {
        "sessions_sampled": totals["sessions"],
        "date_mismatch": totals["date_mismatch"],
        "common_rows": totals["common_rows"],
        "mismatched_rows": totals["mismatched_rows"],
        "mismatch_rate": round(totals["mismatched_rows"] / common_rows, 5),
        "mismatch_by_field": dict(field_counts),
        "rows_only_in_source": totals["only_source"],
        "rows_only_in_local": totals["only_local"],
        "examples": examples,
    }


def floorsheet_day_totals(path: str) -> tuple[date, float, dict[str, int]]:
    import pyarrow.parquet as pq

    table = pq.read_table(path, columns=["symbol", "contract_quantity", "business_date", "data_quality_gap_pct"])
    volumes: Counter[str] = Counter()
    for symbol, quantity in zip(table.column("symbol").to_pylist(), table.column("contract_quantity").to_pylist()):
        volumes[symbol] += int(quantity)
    gaps = [g for g in table.column("data_quality_gap_pct").to_pylist() if g is not None]
    return date_from_partition_path(path), max(gaps) if gaps else 0.0, dict(volumes)


def date_from_partition_path(path: str) -> date:
    match = PARTITION_DATE.search(path)
    if match is None:
        raise ValueError(f"not a year=/month=/day= partition path: {path}")
    return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))


def classify_volume(price_volume: int, floorsheet_volume: int) -> str:
    if price_volume == floorsheet_volume:
        return "exact"
    if floorsheet_volume and abs(price_volume - floorsheet_volume) / max(price_volume, floorsheet_volume) <= 0.01:
        return "within_1pct"
    return "mismatch"


def validate_against_floorsheet(start: date, end: date, root: Path = DEFAULT_FLOORSHEET_ROOT) -> dict[str, Any]:
    files = sorted(glob.glob(str(root / "year=*" / "month=*" / "day=*.parquet")))
    overall: Counter[str] = Counter()
    complete_days: Counter[str] = Counter()
    by_year: dict[str, Counter[str]] = {}
    days_without_prices: list[str] = []
    empty_files: list[str] = []
    day_count = 0
    for path in files:
        day, gap_pct, volumes = floorsheet_day_totals(path)
        if not start <= day <= end:
            continue
        day_count += 1
        if not volumes:
            empty_files.append(day.isoformat())
            continue
        local = _local_rows(day)
        if not local:
            days_without_prices.append(day.isoformat())
            continue
        year = by_year.setdefault(str(day.year), Counter())
        for symbol in set(volumes) | set(local):
            if symbol not in local:
                outcome = "only_floorsheet"
            elif symbol not in volumes:
                outcome = "only_prices"
            else:
                outcome = classify_volume(int(local[symbol]["volume"]), volumes[symbol])
            overall[outcome] += 1
            year[outcome] += 1
            if gap_pct == 0:
                complete_days[outcome] += 1

    def rates(counts: Counter[str]) -> dict[str, Any]:
        compared = counts["exact"] + counts["within_1pct"] + counts["mismatch"]
        return {
            **dict(counts),
            "compared": compared,
            "exact_rate": round(counts["exact"] / compared, 4) if compared else None,
            "mismatch_rate": round(counts["mismatch"] / compared, 4) if compared else None,
        }

    return {
        "floorsheet_days_in_range": day_count,
        "floorsheet_days_without_prices": days_without_prices,
        "empty_floorsheet_files": empty_files,
        "overall": rates(overall),
        "days_with_zero_gap": rates(complete_days),
        "by_year": {year: rates(counts) for year, counts in sorted(by_year.items())},
    }


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description="Validate stored daily prices against the public source and the floorsheet")
    parser.add_argument("check", choices=["overlap", "floorsheet"])
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--every", type=int, default=10)
    parser.add_argument("--floorsheet-root", type=Path, default=DEFAULT_FLOORSHEET_ROOT)
    args = parser.parse_args()
    if args.check == "overlap":
        result = validate_overlap(args.start, args.end, args.every)
    else:
        result = validate_against_floorsheet(args.start, args.end, args.floorsheet_root)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
