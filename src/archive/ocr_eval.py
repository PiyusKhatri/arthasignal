from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
OCR_DIR = ROOT / "docs" / "ocr"
MANIFEST = OCR_DIR / "sample_manifest.json"
GOLD = OCR_DIR / "gold_labels.json"
REPORT = OCR_DIR / "ocr_comparison.json"
ENGINES = ("tesseract", "paddleocr", "surya")
DEVANAGARI = str.maketrans("०१२३४५६७८९", "0123456789")
NUMBER = re.compile(r"\(?-?\d[\d,]*\.?\d*\)?")
LABELS = {
    "net_profit": re.compile(r"net\s*profit|profit\s*/?\s*\(?loss\)?\s*for\s*the\s*(period|year)|profit\s*for\s*the\s*(period|year)|net\s*\(?loss\)?\s*/?\s*profit|खुद\s*नाफा", re.I),
    "eps": re.compile(r"earnings?\s*per\s*share|\bEPS\b|प्रति\s*शेयर\s*आम्दानी", re.I),
    "book_value": re.compile(r"net\s*-?\s*worth\s*per\s*share|book\s*value\s*per\s*share|प्रति\s*शेयर\s*(नेटवर्थ|खुद\s*सम्पत्ति)", re.I),
}
UNIT = re.compile(r"(in\s*'?000|in\s*thousand|'000|हजार)|(in\s*lakh|लाख)|(in\s*million)", re.I)
PROFIT_TOLERANCE = 0.02
RATIO_TOLERANCE = 0.01


def manifest(engine: Any) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        rows = pd.read_sql(text(
            "SELECT d.sha256, d.path, d.symbol, d.published_date, q.fiscal_year, q.quarter, q.net_profit::float AS headline_net_profit "
            "FROM archive_documents d JOIN quarterly_report_announcements q ON q.source = 'sharesansar' AND q.source_id = d.announcement_source_id "
            "WHERE d.source = 'sharesansar' AND (d.url LIKE '%/photos/shares/announcement/%' OR d.url LIKE '%/wp-content/%') "
            "AND d.published_date < DATE '2025-09-30' ORDER BY d.published_date"), connection)
    rows["published_date"] = rows["published_date"].astype(str)
    return rows.drop_duplicates("sha256").to_dict("records")


SLASH_DECIMAL = re.compile(r"((?:रु|रू|Rs)\.?\s*)(\d+)\s*/\s*(\d{2})\b")
YTD_HEADER = re.compile(r"up\s*to\s*this\s*quarter|\(YTD\)|YTD", re.I)
THIS_QUARTER = re.compile(r"this\s*quarter(?!\s*end)", re.I)


def rows_from_text(raw: str) -> list[str]:
    raw = SLASH_DECIMAL.sub(r"\1\2.\3", raw.translate(DEVANAGARI))
    if raw.count("\n\n") > raw.count("\n") / 4:
        return [" ".join(part.split()) for part in re.split(r"\n\s*\n", raw) if part.strip()]
    return [" ".join(line.split()) for line in raw.splitlines() if line.strip()]


def to_number(token: str) -> float | None:
    negative = token.startswith("(") and token.endswith(")") or token.startswith("-")
    cleaned = token.strip("()-").replace(",", "")
    if not cleaned or cleaned.count(".") > 1:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


def values_after(row: str, label: re.Pattern) -> list[float]:
    match = label.search(row)
    if not match:
        return []
    tail = row[match.end():]
    tail = re.sub(r"\([A-Za-z.\s/'%-]*\)|\(\s*[A-Z]\.?[-\d.+\s]*\)", " ", tail)
    out = []
    for token in NUMBER.findall(tail):
        value = to_number(token)
        if value is not None:
            out.append(value)
    return out


def first_value(row: str, label: re.Pattern) -> float | None:
    match = label.search(row)
    if not match:
        return None
    tail = row[match.end():]
    tail = re.sub(r"\([A-Za-z.\s/'%-]*\)|\(\s*[A-Z]\.?[-\d.+\s]*\)", " ", tail)
    for token in NUMBER.findall(tail):
        if re.fullmatch(r"\d\.\d{1,2}", token.strip("()")) and tail.strip().startswith(token):
            continue
        value = to_number(token)
        if value is not None:
            return value
    return None


def unit_multiplier(raw: str) -> float:
    match = UNIT.search(raw)
    if not match:
        return 1.0
    return 1_000.0 if match.group(1) else (100_000.0 if match.group(2) else 1_000_000.0)


def extract(raw: str) -> dict[str, Any]:
    rows = rows_from_text(raw)
    out: dict[str, Any] = {"unit": unit_multiplier(raw)}
    ytd = bool(YTD_HEADER.search(raw)) and bool(THIS_QUARTER.search(raw))
    for field, label in LABELS.items():
        value = None
        for row in rows:
            if field == "net_profit" and ytd:
                values = values_after(row, label)
                if len(values) >= 4:
                    value = values[1]
                    break
                if values:
                    value = values[0]
                    break
                continue
            value = first_value(row, label)
            if value is not None:
                break
        out[field] = value
    out["ytd_layout"] = ytd
    return out


def _close(a: float | None, b: float | None, tolerance: float) -> bool:
    if a is None or b is None:
        return False
    if b == 0:
        return a == 0
    return abs(a - b) <= tolerance * abs(b)


def contains(raw: str, value: float) -> bool:
    digits = raw.translate(DEVANAGARI).replace(",", "")
    target = f"{value:.2f}"
    return target in digits or target.rstrip("0").rstrip(".") in re.findall(r"\d+\.?\d*", digits)


def score(texts: dict[str, dict[str, str]], docs: list[dict[str, Any]], gold: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"documents": len(docs), "gold_documents": len(gold), "engines": {}}
    for engine, by_name in texts.items():
        stats = {"net_profit_vs_headline": [0, 0], "net_profit_vs_gold": [0, 0], "eps_vs_gold": [0, 0], "book_value_vs_gold": [0, 0],
                 "gold_values_present_in_text": [0, 0], "documents_with_text": 0}
        rows = []
        for doc in docs:
            name = Path(doc["path"]).name
            raw = by_name.get(name, "")
            stats["documents_with_text"] += bool(raw.strip())
            got = extract(raw)
            profit = got["net_profit"] * got["unit"] if got["net_profit"] is not None else None
            if doc.get("headline_net_profit") is not None:
                stats["net_profit_vs_headline"][1] += 1
                stats["net_profit_vs_headline"][0] += _close(profit, doc["headline_net_profit"], PROFIT_TOLERANCE)
            label = gold.get(doc["sha256"])
            if label:
                for field, key, tolerance in (("net_profit", "net_profit_vs_gold", PROFIT_TOLERANCE), ("eps", "eps_vs_gold", RATIO_TOLERANCE),
                                              ("book_value", "book_value_vs_gold", RATIO_TOLERANCE)):
                    truth = label.get(field)
                    if truth is None:
                        continue
                    stats[key][1] += 1
                    value = profit if field == "net_profit" else got[field]
                    stats[key][0] += _close(value, truth, tolerance)
                for field in ("eps", "book_value"):
                    if label.get(field) is not None:
                        stats["gold_values_present_in_text"][1] += 1
                        stats["gold_values_present_in_text"][0] += contains(raw, label[field])
            rows.append({"sha256": doc["sha256"], "symbol": doc["symbol"], "published_date": doc["published_date"], **got, "net_profit_scaled": profit})
        out["engines"][engine] = {k: ({"right": v[0], "of": v[1], "accuracy": v[0] / v[1] if v[1] else None} if isinstance(v, list) else v)
                                  for k, v in stats.items()}
        out["engines"][engine]["rows"] = rows
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["manifest", "score"])
    args = parser.parse_args()
    OCR_DIR.mkdir(parents=True, exist_ok=True)
    if args.command == "manifest":
        from src.database.holdout_guard import research_engine

        docs = manifest(research_engine())
        MANIFEST.write_text(json.dumps(docs, indent=1, default=str))
        print(len(docs), "documents")
        return
    docs = json.loads(MANIFEST.read_text())
    gold = {g["sha256"]: g for g in json.loads(GOLD.read_text())["labels"]} if GOLD.exists() else {}
    texts = {}
    for engine in ENGINES:
        path = OCR_DIR / f"text_{engine}.json"
        if path.exists():
            texts[engine] = json.loads(path.read_text())["texts"]
    report = score(texts, docs, gold)
    REPORT.write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({e: {k: v for k, v in s.items() if k != "rows"} for e, s in report["engines"].items()}, indent=1))


if __name__ == "__main__":
    main()
