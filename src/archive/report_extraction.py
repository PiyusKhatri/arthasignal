from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.archive import ocr_eval

DERIVED = Path("~/Desktop/arthasignal-ai/derived/ocr").expanduser()
MANIFEST = DERIVED / "manifest.json"
ACCEPT_THRESHOLD = 0.90
EXTRA_LABELS = {
    "reserves": re.compile(r"reserves?\s*(and|&)\s*surplus|\breserves\b|जगेडा", re.I),
    "npl_ratio": re.compile(r"non\s*-?\s*performing\s*loans?\s*\(?npl\)?\s*to\s*total\s*loans?|\bNPL\b.{0,20}total\s*loan|निष्क्रिय\s*कर्जा", re.I),
    "capital_adequacy": re.compile(r"capital\s*fund\s*to\s*RWA|capital\s*adequacy|पूँजीकोष\s*अनुपात", re.I),
    "dividend_declared": re.compile(r"(proposed|declared)\s*(cash\s*)?dividend|लाभांश", re.I),
}


def build_manifest(research: Any) -> int:
    with research.connect() as connection:
        rows = pd.read_sql(text(
            "SELECT DISTINCT ON (d.sha256) d.sha256, d.path, d.symbol, d.published_date, q.fiscal_year, q.quarter "
            "FROM archive_documents d JOIN quarterly_report_announcements q ON q.source = 'sharesansar' AND q.source_id = d.announcement_source_id "
            "WHERE d.source = 'sharesansar' AND (d.url LIKE '%/photos/shares/announcement/%' OR d.url LIKE '%/wp-content/%') "
            "ORDER BY d.sha256, d.published_date"), connection)
    rows["published_date"] = rows["published_date"].astype(str)
    DERIVED.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(rows.to_dict("records"), default=str))
    return len(rows)


def accepted_fields(comparison: dict[str, Any], engine_name: str) -> dict[str, tuple[bool, str]]:
    stats = comparison["engines"][engine_name]
    measured = {
        "net_profit": [stats["net_profit_vs_headline"], stats["net_profit_vs_gold"]],
        "eps": [stats["eps_vs_gold"]],
        "book_value": [stats["book_value_vs_gold"]],
    }
    out = {}
    for field, parts in measured.items():
        rates = [p["accuracy"] for p in parts if p["accuracy"] is not None]
        worst = min(rates) if rates else None
        ok = worst is not None and worst >= ACCEPT_THRESHOLD
        out[field] = (ok, f"{engine_name}: measured accuracy {worst:.2f}" if worst is not None else "accuracy not measured")
    for field in EXTRA_LABELS:
        out[field] = (False, "accuracy not measured")
    return out


def extract_all(raw: str) -> dict[str, Any]:
    base = ocr_eval.extract(raw)
    rows = ocr_eval.rows_from_text(raw)
    out = {"net_profit": base["net_profit"] * base["unit"] if base["net_profit"] is not None else None, "eps": base["eps"], "book_value": base["book_value"]}
    for field, label in EXTRA_LABELS.items():
        value = None
        for row in rows:
            value = ocr_eval.first_value(row, label)
            if value is not None:
                break
        out[field] = value * base["unit"] if field == "reserves" and value is not None else value
    return out


def store(engine: Engine, engine_name: str, comparison_path: Path = ocr_eval.REPORT) -> dict[str, Any]:
    decisions = accepted_fields(json.loads(comparison_path.read_text()), engine_name)
    manifest = json.loads(MANIFEST.read_text())
    text_dir = DERIVED / engine_name
    added = processed = 0
    with engine.begin() as connection:
        for doc in manifest:
            path = text_dir / f"{doc['sha256']}.txt"
            if not path.exists():
                continue
            processed += 1
            for field, value in extract_all(path.read_text()).items():
                if value is None:
                    continue
                ok, reason = decisions[field]
                added += connection.execute(text(
                    "INSERT INTO report_field_values (document_sha256, symbol, fiscal_year, quarter, field, value, method, accepted, reason, published_date) "
                    "VALUES (:sha, :sym, :fy, :q, :f, :v, :m, :ok, :r, :pd) ON CONFLICT (document_sha256, field, method) DO NOTHING"),
                    {"sha": doc["sha256"], "sym": doc["symbol"], "fy": doc["fiscal_year"], "q": doc["quarter"], "f": field, "v": value,
                     "m": f"ocr:{engine_name}", "ok": ok, "r": reason, "pd": doc["published_date"]}).rowcount
    return {"documents_with_text": processed, "values_added": added, "decisions": {k: v[1] + (" accepted" if v[0] else " flagged") for k, v in decisions.items()}}


def main() -> None:
    from src.database.connection import engine
    from src.database.holdout_guard import research_engine

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["manifest", "store"])
    parser.add_argument("--engine", default="paddleocr_mobile")
    args = parser.parse_args()
    if args.command == "manifest":
        print(build_manifest(research_engine()), "documents")
    else:
        print(json.dumps(store(engine, args.engine), indent=1))


if __name__ == "__main__":
    main()
