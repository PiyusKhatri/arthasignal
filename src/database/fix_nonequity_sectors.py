from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import text

from src.database.instruments import nonequity_with_equity_sector, normalize_sector


def main() -> None:
    parser = argparse.ArgumentParser(description="Give non-equity instruments their own sector label instead of the sponsor's equity sector")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    from src.database.connection import engine

    with engine.begin() as connection:
        rows = nonequity_with_equity_sector(connection)
        changes = [{**r, "to": normalize_sector(r["instrument_type"], r["sector"])} for r in rows]
        if args.apply:
            for change in changes:
                connection.execute(text("UPDATE companies SET sector = :to WHERE symbol = :s"), {"to": change["to"], "s": change["symbol"]})
    summary: dict = {}
    for change in changes:
        key = f"{change['instrument_type']}: {change['sector']} -> {change['to']}"
        summary[key] = summary.get(key, 0) + 1
    out = {"applied": args.apply, "count": len(changes), "summary": summary, "rows": changes}
    if args.report:
        args.report.write_text(json.dumps(out, indent=1, default=str) + "\n")
    print(json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=1))


if __name__ == "__main__":
    main()
