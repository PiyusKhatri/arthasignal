from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Sequence

import duckdb
import pandas as pd

from src.simulation.protocol import Protocol, as_date, load

FLOORSHEET_ROOT = Path(os.environ.get("ARTHASIGNAL_FLOORSHEET_DIR", "~/Desktop/arthasignal-ai/raw/floorsheet")).expanduser()
DERIVED_DIR = Path(os.environ.get("ARTHASIGNAL_DERIVED_DIR", "~/Desktop/arthasignal-ai/derived")).expanduser() / "floorsheet_ohlc"
REPORT_PATH = Path(__file__).resolve().parents[2] / "docs" / "floorsheet_ohlc_verification.json"
FILE_PATTERN = re.compile(r"year=(\d{4})/month=(\d{1,2})/day=(\d{1,2})\.parquet$")
FIELDS = ("open", "high", "low")
SOURCE = "floorsheet_derived"


def floorsheet_files(start: date, end: date, root: Path = FLOORSHEET_ROOT) -> list[tuple[date, Path]]:
    from src.database.holdout_guard import HOLDOUT_START, HoldoutQueryViolation

    if end >= HOLDOUT_START:
        raise HoldoutQueryViolation(f"floorsheet files from {HOLDOUT_START} are holdout data; end {end} is not allowed")
    found = []
    for path in root.glob("year=*/month=*/day=*.parquet"):
        match = FILE_PATTERN.search(path.as_posix())
        if match is None:
            continue
        day = date(*(int(part) for part in match.groups()))
        if start <= day <= end:
            found.append((day, path))
    return sorted(found)


def _trades_sql(files: Sequence[tuple[date, Path]], drop_share: float = 0.0, seed: int = 0, min_quantity: int = 1) -> str:
    paths = ", ".join(f"'{p.as_posix()}'" for _, p in files)
    drop = ""
    if drop_share > 0:
        drop = f"AND hash(business_date || ':' || page_number::VARCHAR || ':{seed}') % 1000000 >= {int(drop_share * 1_000_000)}"
    return (
        "SELECT business_date::DATE AS date, upper(trim(symbol)) AS symbol, contract_rate AS rate, contract_quantity AS quantity, "
        "CAST(transaction_no AS HUGEINT) AS contract, length(transaction_no) AS digits, "
        "CASE WHEN length(transaction_no) = 16 THEN substr(transaction_no, 9, 2) ELSE NULL END AS segment, "
        "substr(transaction_no, 1, 8) = strftime(business_date::DATE, '%Y%m%d') AS prefix_ok, "
        "data_quality_gap_pct AS gap_pct, page_number "
        f"FROM read_parquet([{paths}]) WHERE contract_rate > 0 AND contract_quantity >= {max(1, int(min_quantity))} {drop}"
    )


def connect(work_dir: Path = DERIVED_DIR / "work") -> duckdb.DuckDBPyConnection:
    work_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET temp_directory='{work_dir.as_posix()}'")
    con.execute("SET memory_limit='4GB'")
    con.execute("SET threads=4")
    con.execute("SET preserve_insertion_order=false")
    return con


def derive(
    files: Sequence[tuple[date, Path]],
    drop_share: float = 0.0,
    seed: int = 0,
    con: duckdb.DuckDBPyConnection | None = None,
    min_quantity: int = 1,
) -> pd.DataFrame:
    db = con or connect()
    parts = []
    for month in sorted({(d.year, d.month) for d, _ in files}):
        chunk = [(d, f) for d, f in files if (d.year, d.month) == month]
        parts.append(
            db.execute(
                "SELECT date, symbol, arg_min(rate, contract) AS open, max(rate) AS high, min(rate) AS low, "
                "arg_max(rate, contract) AS last, count(*) AS trades, sum(quantity) AS quantity, "
                "count(DISTINCT contract) = count(*) AS contracts_unique, bool_and(prefix_ok) AS prefix_ok, "
                "max(digits) AS digits, count(DISTINCT segment) AS segments, max(gap_pct) AS gap_pct, "
                "arg_min(rate, hash(contract)) AS random_trade "
                f"FROM ({_trades_sql(chunk, drop_share, seed, min_quantity)}) GROUP BY date, symbol"
            ).df()
        )
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True).sort_values(["date", "symbol"], ignore_index=True)


def published_bars(start: date, end: date) -> pd.DataFrame:
    from sqlalchemy import text

    from src.database.holdout_guard import research_engine

    with research_engine().connect() as connection:
        frame = pd.read_sql(
            text(
                "SELECT upper(trim(symbol)) AS symbol, date, open, high, low, close FROM daily_prices "
                "WHERE date >= :start AND date <= :end AND close > 0"
            ),
            connection,
            params={"start": start, "end": end},
        )
    for column in ("open", "high", "low", "close"):
        frame[column] = frame[column].astype(float)
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


def _rates(derived: pd.DataFrame, published: pd.DataFrame) -> dict[str, Any]:
    merged = derived.merge(published.rename(columns={c: f"{c}_pub" for c in ("open", "high", "low", "close")}), on=["date", "symbol"])
    out: dict[str, Any] = {"symbol_days": int(len(merged))}
    if merged.empty:
        return out
    pairs = {"open": "open", "high": "high", "low": "low", "last_vs_close": ("last", "close"), "random_trade_vs_open": ("random_trade", "open")}
    for name, pair in pairs.items():
        mine, theirs = (pair, pair) if isinstance(pair, str) else pair
        a = merged[mine]
        b = merged[f"{theirs}_pub"]
        diff = (a - b).abs()
        out[name] = {
            "exact": float((diff < 0.005).mean()),
            "within_1pct": float((diff <= 0.01 * b.abs()).mean()),
        }
    return out


def _section(
    files: Sequence[tuple[date, Path]],
    published: pd.DataFrame,
    min_quantity: int,
    share: float,
    seeds: Sequence[int],
    con: duckdb.DuckDBPyConnection,
) -> dict[str, Any]:
    derived = derive(files, con=con, min_quantity=min_quantity)
    derived["date"] = pd.to_datetime(derived["date"])
    section: dict[str, Any] = {
        "min_quantity": min_quantity,
        "derived_symbol_days": int(len(derived)),
        "contract_order": {
            "symbol_days_with_duplicate_contracts": int((~derived["contracts_unique"]).sum()),
            "symbol_days_with_date_prefix_mismatch": int((~derived["prefix_ok"]).sum()),
            "symbol_days_in_more_than_one_segment": int((derived["segments"] > 1).sum()),
            "first_16_digit_date": str(derived.loc[derived["digits"] == 16, "date"].min().date()) if (derived["digits"] == 16).any() else None,
        },
        "overall": _rates(derived, published),
        "by_format": {f"{d}_digit": _rates(derived[derived["digits"] == d], published) for d in (15, 16)},
        "by_year": {str(y): _rates(part, published) for y, part in derived.groupby(derived["date"].dt.year)},
        "by_gap": {},
        "page_drop": {},
    }
    buckets = pd.cut(derived["gap_pct"].fillna(0.0), [-0.001, 0.0, 1.0, 2.0, 100.0], labels=["0", "0-1", "1-2", ">2"])
    for label, part in derived.groupby(buckets, observed=True):
        section["by_gap"][str(label)] = _rates(part, published)
    for seed in seeds:
        dropped = derive(files, share, seed, con, min_quantity)
        dropped["date"] = pd.to_datetime(dropped["date"])
        section["page_drop"][f"seed_{seed}"] = {
            "overall": _rates(dropped, published),
            "15_digit": _rates(dropped[dropped["digits"] == 15], published),
            "lost_symbol_days": int(len(derived) - len(dropped)),
        }
    return section


def odd_lot_share(files: Sequence[tuple[date, Path]], min_quantity: int, con: duckdb.DuckDBPyConnection) -> dict[str, float]:
    out = {}
    for year in sorted({d.year for d, _ in files}):
        chunk = [(d, f) for d, f in files if d.year == year]
        out[str(year)] = float(
            con.execute(f"SELECT avg(CASE WHEN quantity < {int(min_quantity)} THEN 1.0 ELSE 0.0 END) FROM ({_trades_sql(chunk)})").fetchone()[0]
        )
    return out


def verify(protocol: Protocol | None = None, seeds: Sequence[int] = (1, 2, 3)) -> dict[str, Any]:
    p = protocol or load()
    rule = p.raw["floorsheet_ohlc"]
    start = as_date(rule["verification"]["start"])
    end = as_date(rule["verification"]["end"])
    share = float(rule["acceptance"]["page_drop_share"])
    min_quantity = int(rule["min_quantity"])
    files = floorsheet_files(start, end)
    con = connect()
    published = published_bars(start, end)
    report: dict[str, Any] = {
        "window": [start.isoformat(), end.isoformat()],
        "files": len(files),
        "published_symbol_days": int(len(published)),
        "all_trades": _section(files, published, 1, share, seeds, con),
        "board_lots": _section(files, published, min_quantity, share, seeds, con),
    }
    early_start = as_date(p.raw["periods"]["learning"]["start"])
    early_files = floorsheet_files(early_start, p.real_open_start - timedelta(days=1))
    early = derive(early_files, con=con, min_quantity=min_quantity)
    early["date"] = pd.to_datetime(early["date"])
    early_rates = _rates(early, published_bars(early_start, p.real_open_start - timedelta(days=1)))
    report["before_real_open"] = {
        "files": len(early_files),
        "symbol_days": early_rates["symbol_days"],
        "last_vs_close": early_rates.get("last_vs_close"),
        "random_trade_vs_stored_open": early_rates.get("random_trade_vs_open"),
    }
    report["odd_lot_trade_share"] = odd_lot_share(early_files + files, min_quantity, con)
    report["decision_all_trades"] = decide(report["all_trades"], rule["acceptance"])
    report["decision"] = decide(report["board_lots"], rule["acceptance"])
    return report


def decide(report: dict[str, Any], acceptance: dict[str, Any]) -> dict[str, Any]:
    decision: dict[str, Any] = {}
    for field in FIELDS:
        checks = {}
        for scope in ("overall", "15_digit"):
            rates = report["overall"] if scope == "overall" else report["by_format"]["15_digit"]
            checks[f"{scope}_exact"] = rates[field]["exact"] >= acceptance["exact_min"]
            checks[f"{scope}_within_1pct"] = rates[field]["within_1pct"] >= acceptance["within_1pct_min"]
            for seed, drop in report["page_drop"].items():
                d = drop["overall"] if scope == "overall" else drop["15_digit"]
                checks[f"{scope}_page_drop_{seed}"] = d[field]["within_1pct"] >= acceptance["page_drop_within_1pct_min"]
        decision[field] = {"use": all(checks.values()), "failed": sorted(k for k, ok in checks.items() if not ok)}
    return decision


def build(protocol: Protocol | None = None, out_dir: Path = DERIVED_DIR, end: date | None = None) -> dict[str, Any]:
    p = protocol or load()
    rule = p.raw["floorsheet_ohlc"]
    raw = FLOORSHEET_ROOT.resolve()
    target = out_dir.expanduser().resolve()
    if target == raw or raw in target.parents:
        raise ValueError(f"refusing to write inside the floorsheet directory {raw}")
    start = as_date(p.raw["periods"]["learning"]["start"])
    end = end or as_date(rule["verification"]["end"])
    bars = derive(floorsheet_files(start, end), con=connect(target / "work"), min_quantity=int(rule["min_quantity"]))
    bars["source"] = SOURCE
    bars["before_real_open"] = pd.to_datetime(bars["date"]).dt.date < p.real_open_start
    bars = bars.drop(columns=["random_trade"])
    target.mkdir(parents=True, exist_ok=True)
    path = target / "bars.parquet"
    bars.to_parquet(path, index=False)
    return {"path": str(path), "rows": int(len(bars)), "before_real_open": int(bars["before_real_open"].sum()), "first": str(bars["date"].min()), "last": str(bars["date"].max())}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["verify", "build"])
    parser.add_argument("--end", type=date.fromisoformat)
    args = parser.parse_args()
    if args.command == "verify":
        report = verify()
        REPORT_PATH.write_text(json.dumps(report, indent=1) + "\n")
        print(json.dumps({k: report[k] for k in ("before_real_open", "odd_lot_trade_share", "decision_all_trades", "decision")}, indent=1))
    else:
        print(json.dumps(build(end=args.end), indent=1))


if __name__ == "__main__":
    main()
