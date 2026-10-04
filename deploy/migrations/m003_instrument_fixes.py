from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

NAME = "m003_instrument_fixes"
LOCK_KEY = 820_261_003
EVIDENCE = ROOT / "docs" / "instrument_fixes.json"
MIN_FLOORSHEET_AGREEMENT = 0.9
SYMBOL_TABLES = ("daily_prices", "technical_signals", "corporate_actions", "dividend_declarations", "quarterly_report_announcements",
                 "fundamentals", "promoter_holding", "scorecard_calls", "signal_calls", "holdings", "watchlists", "price_alerts")

LOG_DDL = """
CREATE TABLE IF NOT EXISTS ops_migration_runs (
    id          BIGSERIAL PRIMARY KEY,
    name        VARCHAR(80) NOT NULL,
    ran_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    dry_run     BOOLEAN NOT NULL,
    rows_changed INTEGER NOT NULL,
    detail      JSONB NOT NULL
)
"""


def _references(connection, symbol: str) -> int:
    total = 0
    for table in SYMBOL_TABLES:
        if connection.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}).scalar() is None:
            continue
        total += connection.execute(text(f"SELECT count(*) FROM {table} WHERE symbol = :s"), {"s": symbol}).scalar_one()
    return total


def run(dry_run: bool) -> dict:
    from src.database.connection import engine

    evidence = json.loads(EVIDENCE.read_text())
    changes: dict[str, list] = {"debentures": [], "truncated_rows_moved": [], "truncated_companies_removed": [], "truncated_companies_relabelled": [], "blank_rows_moved": [],
                                "blank_rows_inserted": [], "blank_company_removed": [], "sectors": []}
    skipped: list[dict] = []
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": LOCK_KEY})
        before = connection.execute(text("SELECT count(*) FROM companies WHERE instrument_type = 'Equity' AND sector IS NULL")).scalar_one()
        for item in evidence["debenture_relabels"]:
            n = connection.execute(text("UPDATE companies SET instrument_type = :t, sector = :s WHERE symbol = :y AND instrument_type = 'Equity'"),
                                   {"t": item["instrument_type"], "s": item["sector"], "y": item["symbol"]}).rowcount
            if n:
                changes["debentures"].append(item["symbol"])
        for item in evidence["truncated_symbols"]:
            agree = item["floorsheet_days_agree"] / item["floorsheet_days_checked"] if item["floorsheet_days_checked"] else 0.0
            if agree < MIN_FLOORSHEET_AGREEMENT or item["rows_already_under_full"]:
                skipped.append({"symbol": item["short"], "reason": f"floorsheet agreement {agree:.2f} or {item['rows_already_under_full']} clashing rows"})
                continue
            full_exists = connection.execute(text("SELECT count(*) FROM companies WHERE symbol = :f"), {"f": item["full"]}).scalar_one()
            if not full_exists:
                skipped.append({"symbol": item["short"], "reason": f"{item['full']} is not in companies"})
                continue
            n = connection.execute(text(
                "UPDATE daily_prices p SET symbol = :f WHERE p.symbol = :s "
                "AND NOT EXISTS (SELECT 1 FROM daily_prices q WHERE q.symbol = :f AND q.date = p.date)"), {"f": item["full"], "s": item["short"]}).rowcount
            if n:
                changes["truncated_rows_moved"].append({"from": item["short"], "to": item["full"], "rows": n})
            if _references(connection, item["short"]) == 0:
                if connection.execute(text("DELETE FROM companies WHERE symbol = :s"), {"s": item["short"]}).rowcount:
                    changes["truncated_companies_removed"].append(item["short"])
            else:
                full_type = connection.execute(text("SELECT instrument_type, sector FROM companies WHERE symbol = :f"), {"f": item["full"]}).one()
                n = connection.execute(text("UPDATE companies SET instrument_type = :t, sector = :s WHERE symbol = :y AND instrument_type = 'Equity'"),
                                       {"t": full_type[0], "s": full_type[1], "y": item["short"]}).rowcount
                if n:
                    changes["truncated_companies_relabelled"].append({"symbol": item["short"], "as": item["full"],
                                                                       "rows_left": _references(connection, item["short"])})
        for row in evidence["blank_symbol_prices"]:
            if not row["symbol"]:
                skipped.append({"symbol": "", "date": row["date"], "reason": row["method"]})
                continue
            n = connection.execute(text(
                "UPDATE daily_prices p SET symbol = :y WHERE p.symbol = '' AND p.date = :d AND p.close = :c AND p.volume = :v "
                "AND NOT EXISTS (SELECT 1 FROM daily_prices q WHERE q.symbol = :y AND q.date = :d)"),
                {"y": row["symbol"], "d": row["date"], "c": row["close"], "v": row["volume"]}).rowcount
            if n:
                changes["blank_rows_moved"].append({"date": row["date"], "symbol": row["symbol"]})
        for row in evidence["blank_symbol_lost_rows"]:
            n = connection.execute(text(
                "INSERT INTO daily_prices (symbol, date, open, high, low, close, volume, turnover) "
                "VALUES (:y, :d, :o, :h, :l, :c, :v, :t) ON CONFLICT (symbol, date) DO NOTHING"),
                {"y": row["symbol"], "d": row["date"], "o": row["open"], "h": row["high"], "l": row["low"], "c": row["close"],
                 "v": row["volume"], "t": row["turnover"]}).rowcount
            if n:
                changes["blank_rows_inserted"].append({"date": row["date"], "symbol": row["symbol"]})
        if _references(connection, "") == 0:
            if connection.execute(text("DELETE FROM companies WHERE symbol = ''")).rowcount:
                changes["blank_company_removed"].append("")
        for item in evidence["sector_assignments"]:
            if not item["sector"]:
                skipped.append({"symbol": item["symbol"], "reason": "no confirmed sector"})
                continue
            n = connection.execute(text("UPDATE companies SET sector = :s WHERE symbol = :y AND sector IS NULL AND instrument_type = 'Equity'"),
                                   {"s": item["sector"], "y": item["symbol"]}).rowcount
            if n:
                connection.execute(text("INSERT INTO company_sector_sources (symbol, source, url, http_status, page_title, sector_raw) "
                                        "VALUES (:y, 'nrb', :u, 200, :t, :raw)"),
                                   {"y": item["symbol"], "u": item["class_source"]["url"], "t": item["class_source"]["context"],
                                    "raw": f"class {item['class_source']['class']}"})
                connection.execute(text("INSERT INTO company_sector_assignments (symbol, sector, evidence) VALUES (:y, :s, CAST(:e AS jsonb))"),
                                   {"y": item["symbol"], "s": item["sector"], "e": json.dumps({**item, "applied_by": NAME})})
                changes["sectors"].append({"symbol": item["symbol"], "sector": item["sector"]})
        after = connection.execute(text("SELECT count(*) FROM companies WHERE instrument_type = 'Equity' AND sector IS NULL")).scalar_one()
        blank_left = connection.execute(text("SELECT count(*) FROM daily_prices WHERE symbol = ''")).scalar_one()
        total = sum(len(v) for v in changes.values())
        report = {"migration": NAME, "dry_run": dry_run, "changes": changes, "skipped": skipped,
                  "equities_without_sector_before": before, "equities_without_sector_after": after, "blank_symbol_price_rows_left": blank_left,
                  "status": "already applied: nothing to do" if not total else ("dry run: rolled back" if dry_run else "applied")}
        if total and not dry_run:
            connection.execute(text(LOG_DDL))
            connection.execute(text("INSERT INTO ops_migration_runs (name, dry_run, rows_changed, detail) VALUES (:n, :d, :c, CAST(:j AS jsonb))"),
                               {"n": NAME, "d": dry_run, "c": total, "j": json.dumps(report, default=str)})
        if dry_run:
            transaction.rollback()
        else:
            transaction.commit()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Relabel debentures, merge truncated symbols, repair blank-symbol prices and add NRB-evidenced sectors (idempotent)")
    parser.add_argument("--dry-run", action="store_true")
    report = run(parser.parse_args().dry_run)
    print(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main()
