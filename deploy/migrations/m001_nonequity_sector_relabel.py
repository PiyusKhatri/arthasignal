from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text

NAME = "m001_nonequity_sector_relabel"
LOCK_KEY = 820_261_001
EQUITY_SECTORS = (
    "Commercial Banks", "Development Banks", "Finance", "Hotels And Tourism", "Hydro Power", "Investment", "Life Insurance",
    "Manufacturing And Processing", "Microfinance", "Non Life Insurance", "Others", "Tradings",
)
LABELS = {"Mutual Funds": "Mutual Fund", "Non-Convertible Debentures": "Debenture", "Promoter Shares": "Promoter Share",
          "Preference Shares": "Preference Share"}

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

COUNT = """
SELECT instrument_type, sector, count(*) AS n FROM companies
WHERE instrument_type <> 'Equity' AND sector = ANY(:s)
GROUP BY 1, 2 ORDER BY 1, 2
"""

UPDATE = """
UPDATE companies SET sector = CASE instrument_type
    WHEN 'Mutual Funds' THEN 'Mutual Fund'
    WHEN 'Non-Convertible Debentures' THEN 'Debenture'
    WHEN 'Promoter Shares' THEN 'Promoter Share'
    WHEN 'Preference Shares' THEN 'Preference Share'
    ELSE 'Non-Equity' END
WHERE instrument_type <> 'Equity' AND sector = ANY(:s)
RETURNING symbol, instrument_type
"""


def rows(connection) -> list[dict]:
    return [dict(r) for r in connection.execute(text(COUNT), {"s": list(EQUITY_SECTORS)}).mappings()]


def run(dry_run: bool) -> dict:
    from src.database.connection import engine

    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": LOCK_KEY})
        before = rows(connection)
        changed = []
        if not dry_run and before:
            changed = [dict(r) for r in connection.execute(text(UPDATE), {"s": list(EQUITY_SECTORS)}).mappings()]
        after = rows(connection) if not dry_run else before
        report = {
            "migration": NAME,
            "dry_run": dry_run,
            "before": {"total": sum(r["n"] for r in before), "by_type_and_sector": before},
            "rows_changed": len(changed),
            "changed_by_type": {t: sum(1 for c in changed if c["instrument_type"] == t) for t in sorted({c["instrument_type"] for c in changed})},
            "after": {"total": sum(r["n"] for r in after), "by_type_and_sector": after},
            "status": "already fixed: nothing to do" if not before else ("dry run: nothing written" if dry_run else "applied"),
        }
        if changed:
            connection.execute(text(LOG_DDL))
            connection.execute(
                text("INSERT INTO ops_migration_runs (name, dry_run, rows_changed, detail) VALUES (:n, :d, :c, CAST(:j AS jsonb))"),
                {"n": NAME, "d": dry_run, "c": len(changed), "j": json.dumps(report, default=str)},
            )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Relabel non-equity instruments that carry an equity sector (idempotent)")
    parser.add_argument("--dry-run", action="store_true")
    report = run(parser.parse_args().dry_run)
    print(json.dumps(report, indent=1, default=str))
    sys.exit(1 if report["after"]["total"] and not report["dry_run"] else 0)


if __name__ == "__main__":
    main()
