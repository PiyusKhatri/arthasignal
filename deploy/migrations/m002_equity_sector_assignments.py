from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

NAME = "m002_equity_sector_assignments"
LOCK_KEY = 820_261_002
EVIDENCE = ROOT / "docs" / "sector_sources.json"


def assignments() -> list[dict]:
    data = json.loads(EVIDENCE.read_text())
    return [r for r in data["results"] if r["result"] == "assigned"]


def run(dry_run: bool) -> dict:
    from src.database.connection import engine
    from src.scrapers.sector_sources import DDL

    rows = assignments()
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": LOCK_KEY})
        current = {r[0]: r[1] for r in connection.execute(
            text("SELECT symbol, sector FROM companies WHERE symbol = ANY(:s) AND instrument_type = 'Equity'"), {"s": [r["symbol"] for r in rows]})}
        pending = [r for r in rows if r["symbol"] in current and current[r["symbol"]] is None]
        conflicting = [{"symbol": r["symbol"], "database": current[r["symbol"]], "evidence": r["sector"]} for r in rows
                       if r["symbol"] in current and current[r["symbol"]] not in (None, r["sector"])]
        missing = [r["symbol"] for r in rows if r["symbol"] not in current]
        before = connection.execute(text("SELECT count(*) FROM companies WHERE instrument_type = 'Equity' AND sector IS NULL")).scalar_one()
        changed = 0
        if pending and not dry_run:
            raw = connection.connection
            with raw.cursor() as cursor:
                cursor.execute(DDL)
            for r in pending:
                changed += connection.execute(text("UPDATE companies SET sector = :s WHERE symbol = :y AND sector IS NULL AND instrument_type = 'Equity'"),
                                              {"s": r["sector"], "y": r["symbol"]}).rowcount
                for source, ev in r["evidence"].items():
                    connection.execute(text("INSERT INTO company_sector_sources (symbol, source, url, http_status, page_title, sector_raw) "
                                            "VALUES (:y, :src, :u, :h, :t, :raw)"),
                                       {"y": r["symbol"], "src": source, "u": ev["url"], "h": ev["http"], "t": ev["title"], "raw": ev["sector_raw"]})
                connection.execute(text("INSERT INTO company_sector_assignments (symbol, sector, evidence) VALUES (:y, :s, CAST(:e AS jsonb))"),
                                   {"y": r["symbol"], "s": r["sector"], "e": json.dumps({**r["evidence"], "applied_by": NAME})})
        after = connection.execute(text("SELECT count(*) FROM companies WHERE instrument_type = 'Equity' AND sector IS NULL")).scalar_one()
    return {"migration": NAME, "dry_run": dry_run, "evidence_assignments": len(rows), "pending_before": len(pending),
            "rows_changed": changed, "equities_without_sector_before": before, "equities_without_sector_after": after if not dry_run else before,
            "conflicts_left_untouched": conflicting, "symbols_not_in_database": missing,
            "status": "already applied: nothing to do" if not pending else ("dry run: nothing written" if dry_run else "applied")}


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply the source-evidenced sectors of formerly sectorless equities (idempotent)")
    parser.add_argument("--dry-run", action="store_true")
    report = run(parser.parse_args().dry_run)
    print(json.dumps(report, indent=1, default=str))
    sys.exit(1 if report["conflicts_left_untouched"] else 0)


if __name__ == "__main__":
    main()
