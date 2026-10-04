from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import text

from src.database.instruments import PROMOTER, is_promoter, known_symbols, promoter_base


def candidates(connection) -> list[dict]:
    known = known_symbols(connection)
    rows = connection.execute(text("SELECT symbol, company_name, instrument_type, status FROM companies ORDER BY symbol")).all()
    return [
        {"symbol": s, "company_name": n, "from": t, "status": st, "base": promoter_base(s, known),
         "name_says_promoter": bool(n and "promot" in n.lower())}
        for s, n, t, st in rows
        if t != PROMOTER and is_promoter(s, n, known)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Reclassify promoter-share symbols stored as another instrument type")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    from src.database.connection import engine

    with engine.begin() as connection:
        found = candidates(connection)
        if args.apply and found:
            connection.execute(text("UPDATE companies SET instrument_type = :p WHERE symbol = ANY(:s)"),
                               {"p": PROMOTER, "s": [f["symbol"] for f in found]})
    out = {"applied": args.apply, "count": len(found), "rows": found}
    if args.report:
        args.report.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"applied": args.apply, "count": len(found), "symbols": [f["symbol"] for f in found]}, indent=1))


if __name__ == "__main__":
    main()
