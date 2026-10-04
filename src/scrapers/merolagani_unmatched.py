from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import text

from src.scrapers.quarterly_reports_collector import MEROLAGANI_DETAIL_URL, Collector, _name_key

DEFAULT_CSV = Path("docs/merolagani_unmatched.csv")
TITLE_NAME = re.compile(r"quarterly report\s+of\s+(.+?)\s+for the fiscal year", re.I)


def _detail(collector: Collector, source_id: str) -> tuple[str | None, str | None]:
    page = collector.get(MEROLAGANI_DETAIL_URL.format(id=source_id))
    body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page.text))
    match = re.search(r"Symbol\s+([A-Z0-9]{2,20})\s+\(([^)]*)\)", body)
    return (match.group(1), match.group(2).strip()) if match else (None, None)


def propose(engine: Any, delay: float) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        pending = connection.execute(
            text(
                "SELECT m.company_name, m.checked_source_id, count(a.id) FROM merolagani_company_symbols m "
                "JOIN quarterly_report_announcements a ON a.company_name = m.company_name AND a.source = 'merolagani' "
                "WHERE m.symbol IS NULL GROUP BY 1, 2 ORDER BY 3 DESC, 1"
            )
        ).all()
        companies = connection.execute(text("SELECT symbol, company_name, status, instrument_type FROM companies")).all()
        history = connection.execute(text("SELECT old_symbol, new_symbol, event_type::text, effective_date FROM symbol_history")).all()
    by_symbol = {s: (n, st, it) for s, n, st, it in companies}
    renamed = {old: (new, kind, str(day)) for old, new, kind, day in history}
    keys = {_name_key(n): s for s, n, _, _ in companies if n}
    collector = Collector(engine, delay)
    out = []
    for name, source_id, rows in pending:
        ml_symbol, ml_name = _detail(collector, source_id)
        inner = TITLE_NAME.search(name)
        lookup_name = inner.group(1) if inner else re.sub(r"\(.*?\)", " ", name)
        best = difflib.get_close_matches(_name_key(lookup_name), list(keys), n=1, cutoff=0.0)
        score = difflib.SequenceMatcher(None, _name_key(lookup_name), best[0]).ratio() if best else 0.0
        fuzzy_symbol = keys[best[0]] if best else None
        proposal, evidence, confidence = None, "", "none"
        if ml_symbol and ml_symbol in by_symbol:
            proposal, confidence = ml_symbol, "certain"
            evidence = f"Merolagani detail page shows symbol {ml_symbol}, which exists in companies ({by_symbol[ml_symbol][0]}, status {by_symbol[ml_symbol][1]})"
        elif ml_symbol and ml_symbol in renamed:
            new, kind, day = renamed[ml_symbol]
            proposal, confidence = None, "successor_only"
            evidence = (f"Merolagani symbol {ml_symbol} is absent from companies; symbol_history records {ml_symbol} -> {new} ({kind}, {day}). "
                        f"{new} is a successor, not the same issuer, so its reports are not {ml_symbol}'s")
        elif ml_symbol:
            proposal, confidence = None, "absent"
            evidence = (f"Merolagani symbol {ml_symbol} ({ml_name}) is in neither companies nor symbol_history; "
                        f"closest listed name {fuzzy_symbol} (similarity {score:.2f}) is shown for review only")
        else:
            proposal, confidence = None, "absent"
            evidence = f"no symbol on the Merolagani page; closest listed name {fuzzy_symbol} (similarity {score:.2f}) is shown for review only"
        out.append({"company_name": name, "rows": int(rows), "checked_source_id": source_id, "merolagani_symbol": ml_symbol,
                    "merolagani_name": ml_name, "proposed_symbol": proposal, "closest_listed_name_symbol": fuzzy_symbol,
                    "proposed_company": by_symbol.get(proposal, (None,))[0] if proposal else None,
                    "name_similarity": round(score, 2), "confidence": confidence, "evidence": evidence})
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Propose symbols for Merolagani company names that did not resolve")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--delay", type=float, default=3.0)
    args = parser.parse_args()
    from src.database.connection import engine

    rows = propose(engine, args.delay)
    with args.csv.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary: dict[str, int] = {}
    for row in rows:
        summary[row["confidence"]] = summary.get(row["confidence"], 0) + 1
    print(json.dumps({"names": len(rows), "by_confidence": summary}, indent=2))


if __name__ == "__main__":
    main()
