from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable

from src.archive import ocr_eval

ROOT = Path(__file__).resolve().parents[2]
DERIVED = Path("~/Desktop/arthasignal-ai/derived/ocr").expanduser()
LABEL_DIR = ROOT / "docs" / "labels"
REPORT = ROOT / "docs" / "fundamentals_precision.json"
ENGINES = ("paddleocr_mobile", "surya", "tesseract")
FIELDS = ("net_profit", "eps", "book_value_per_share", "net_worth", "reserves", "paid_up_capital", "npl_ratio", "capital_adequacy")
UNIT = {"rupees": 1.0, "thousands": 1_000.0, "lakhs": 100_000.0, "millions": 1_000_000.0}
AMOUNTS = {"net_profit", "net_worth", "reserves", "paid_up_capital"}
PAR_VALUE = 100.0
RANGES = {"eps": (-500.0, 2000.0), "book_value_per_share": (-500.0, 5000.0), "npl_ratio": (0.0, 100.0), "capital_adequacy": (0.0, 100.0),
          "net_profit": (-1e11, 1e11), "net_worth": (-1e11, 1e12), "reserves": (-1e11, 1e12), "paid_up_capital": (1e6, 1e12)}
LABELS = {
    "net_profit": ocr_eval.LABELS["net_profit"],
    "eps": ocr_eval.LABELS["eps"],
    "book_value_per_share": ocr_eval.LABELS["book_value"],
    "net_worth": re.compile(r"total\s*equity|net\s*worth(?!\s*per)|शेयरधनी\s*कोष", re.I),
    "reserves": re.compile(r"reserves?\s*(and|&)\s*surplus|\breserves\b|जगेडा", re.I),
    "paid_up_capital": re.compile(r"paid[\s-]*up\s*capital|share\s*capital|चुक्ता\s*पूँजी", re.I),
    "npl_ratio": re.compile(r"non\s*-?\s*performing\s*loans?.{0,30}total\s*loan|\bNPL\b.{0,20}total\s*loan", re.I),
    "capital_adequacy": re.compile(r"capital\s*fund\s*to\s*RWA|capital\s*adequacy", re.I),
}


def read_values(raw: str) -> dict[str, float | None]:
    base = ocr_eval.extract(raw)
    rows = ocr_eval.rows_from_text(raw)
    out: dict[str, float | None] = {}
    for field, label in LABELS.items():
        if field in ("net_profit", "eps", "book_value_per_share"):
            key = {"book_value_per_share": "book_value"}.get(field, field)
            value = base[key]
        else:
            value = None
            for row in rows:
                value = ocr_eval.first_value(row, label)
                if value is not None:
                    break
        if value is not None and field in AMOUNTS:
            value = value * base["unit"]
        out[field] = value
    return out


def agree(a: float | None, b: float | None, field: str) -> bool:
    if a is None or b is None:
        return False
    if field in AMOUNTS:
        return abs(a - b) <= max(1.0, 0.001 * abs(b))
    return abs(a - b) <= 0.005 + 1e-9


def consensus(values: dict[str, dict[str, float | None]], field: str) -> float | None:
    engines = [e for e in values if values[e].get(field) is not None]
    for i, first in enumerate(engines):
        for second in engines[i + 1:]:
            if agree(values[first][field], values[second][field], field):
                return values[first][field]
    return None


def checks(value: dict[str, float | None], context: dict[str, Any]) -> dict[str, list[str]]:
    failed: dict[str, list[str]] = {f: [] for f in FIELDS}
    for field, (low, high) in RANGES.items():
        v = value.get(field)
        if v is not None and not low <= v <= high:
            failed[field].append("range")
    shares = value["paid_up_capital"] / PAR_VALUE if value.get("paid_up_capital") else None
    quarter = context.get("quarter") or 4
    if shares and value.get("eps") is not None and value.get("net_profit") is not None:
        plain = value["net_profit"] / shares
        annual = plain * 4 / quarter
        if not any(abs(value["eps"] - x) <= max(0.05, 0.05 * abs(x)) for x in (plain, annual)):
            failed["eps"].append("eps_vs_profit_and_shares")
    if shares and value.get("book_value_per_share") is not None and value.get("net_worth") is not None:
        if abs(value["book_value_per_share"] - value["net_worth"] / shares) > max(0.05, 0.02 * abs(value["net_worth"] / shares)):
            failed["book_value_per_share"].append("book_value_vs_net_worth_and_shares")
    headline = context.get("headline_net_profit")
    if headline is not None and value.get("net_profit") is not None:
        if abs(value["net_profit"] - headline) > 0.02 * abs(headline):
            failed["net_profit"].append("headline_mismatch")
    later = context.get("next_report_profit_row") or []
    if later and value.get("net_profit") is not None and context.get("unit_next"):
        if not any(abs(x * context["unit_next"] - value["net_profit"]) <= max(1.0, 0.005 * abs(value["net_profit"])) for x in later):
            failed["net_profit"].append("not_in_next_report_previous_quarter")
    return failed


def wilson(right: int, total: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if total == 0:
        return None, None
    p = right / total
    centre = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return max(0.0, centre - half), min(1.0, centre + half)


def gold_value(label: dict[str, Any], field: str) -> float | None:
    v = label.get(field)
    if v is None or label.get(f"{field}_absent"):
        return None
    if field in AMOUNTS:
        return v * UNIT.get(label.get("unit") or "rupees", 1.0)
    return v


def correct(got: float | None, truth: float | None, field: str) -> bool:
    if got is None or truth is None:
        return False
    if field in AMOUNTS:
        return abs(got - truth) <= max(1.0, 0.005 * abs(truth))
    return abs(got - truth) <= 0.005 + 1e-9


def measure(rows: Iterable[dict[str, Any]], methods: tuple[str, ...]) -> dict[str, Any]:
    rows = list(rows)
    out: dict[str, Any] = {}
    for method in methods:
        out[method] = {}
        for field in FIELDS:
            produced = right = present = 0
            for row in rows:
                truth = gold_value(row["label"], field)
                present += truth is not None
                got = row["methods"].get(method, {}).get(field)
                if got is None:
                    continue
                produced += 1
                right += correct(got, truth, field)
            low, high = wilson(right, produced)
            out[method][field] = {"present_in_gold": present, "produced": produced, "right": right,
                                  "precision": right / produced if produced else None, "precision_ci95": [low, high],
                                  "coverage": right / present if present else None, "usable": bool(produced and right / produced >= 0.99)}
    return out


def method_values(sha: str, context: dict[str, Any]) -> dict[str, dict[str, float | None]]:
    per_engine = {}
    for engine in ENGINES:
        path = DERIVED / engine / f"{sha}.txt"
        if path.exists():
            per_engine[engine] = read_values(path.read_text())
    pdf = DERIVED / "text_pdf" / f"{sha}.txt"
    methods: dict[str, dict[str, float | None]] = dict(per_engine)
    if pdf.exists():
        methods["text_pdf"] = read_values(pdf.read_text())
    agreed = {field: consensus(per_engine, field) for field in FIELDS}
    methods["consensus"] = agreed
    failed = checks(agreed, context)
    methods["consensus_checked"] = {f: (v if not failed[f] else None) for f, v in agreed.items()}
    if "text_pdf" in methods:
        pdf_failed = checks(methods["text_pdf"], context)
        methods["text_pdf_checked"] = {f: (v if not pdf_failed[f] else None) for f, v in methods["text_pdf"].items()}
    methods["headline"] = {f: (context.get("headline_net_profit") if f == "net_profit" else None) for f in FIELDS}
    return methods


def run() -> dict[str, Any]:
    label_set = {item["label_id"]: item for item in json.loads((LABEL_DIR / "label_set.json").read_text())}
    rows = []
    for path in sorted((LABEL_DIR / "labels").glob("L*.json")):
        label = json.loads(path.read_text())
        item = label_set.get(label["label_id"])
        if item is None or label.get("legibility") == "unreadable" or label.get("errors"):
            continue
        quarter = int(label["quarter"]) if str(label.get("quarter") or "").isdigit() else None
        context = {"quarter": quarter if label.get("period") != "annual" else 4, "headline_net_profit": item.get("headline_net_profit")}
        rows.append({"label": label, "methods": method_values(item["sha256"], context)})
    methods = ("tesseract", "paddleocr_mobile", "surya", "text_pdf", "text_pdf_checked", "consensus", "consensus_checked", "headline")
    report = {"labelled_reports_used": len(rows), "rule": "a field is used only if precision >= 0.99; Wilson 95% intervals reported",
              "results": measure(rows, methods)}
    REPORT.write_text(json.dumps(report, indent=1))
    return report


def main() -> None:
    argparse.ArgumentParser().parse_args()
    report = run()
    print("labelled reports used:", report["labelled_reports_used"])
    for method, fields in report["results"].items():
        cells = []
        for field, s in fields.items():
            if s["produced"]:
                cells.append(f"{field} {s['right']}/{s['produced']} ({s['precision']:.2f}, ci {s['precision_ci95'][0]:.2f})")
        print(method, "|", "; ".join(cells) if cells else "no values")


if __name__ == "__main__":
    main()
