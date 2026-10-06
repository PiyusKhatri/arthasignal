from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from src.archive import fundamentals_quality as fq
from src.archive import statement_text as st

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "docs" / "fundamentals_causes.json"
RECHECK = ROOT / "docs" / "labels" / "RECHECK.md"
TEXT_SOURCES = ("tesseract", "paddleocr_mobile", "text_pdf")
BEFORE = ("headline", "tesseract", "paddleocr_mobile", "surya", "text_pdf")
AFTER = ("tesseract_v2", "paddleocr_mobile_v2", "text_pdf_v2")
CLASSIFIED = BEFORE + AFTER
CAUSES = ("unit_scale", "period", "consolidation", "sign", "wrong_row", "digit_misread", "nepali_numeral", "headline_rounding",
          "likely_label_error", "unexplained")
SCALES = (1.0, 1e3, 1e5, 1e6, 1e7)
LOOKALIKE = {frozenset(p) for p in (("1", "9"), ("4", "8"), ("7", "0"), ("6", "8"), ("5", "4"), ("2", "7"), ("3", "2"), ("9", "6"))}
OWNERS = re.compile(r"owners?\s*of|attributable|equity\s*holders|non[\s-]*controlling|minority", re.I)


def headline_step(value: float) -> float:
    return 1e7 if abs(value) >= 1e9 else 1e4


def close(a: float, b: float, amount: bool) -> bool:
    if amount:
        return abs(a - b) <= max(0.5, 0.0005 * abs(b))
    return abs(a - b) <= 0.005 + 1e-9


def occurrences(texts: dict[str, str], value: float, amount: bool, scales: tuple[float, ...] = SCALES) -> list[dict[str, Any]]:
    out = []
    for source, raw in texts.items():
        for i, row in enumerate(st.rows(raw)):
            for number, token in st.numbers(row):
                for scale in (scales if amount else (1.0,)):
                    if close(number * scale, value, amount) or close(-number * scale, value, amount) and token.startswith("("):
                        out.append({"source": source, "row": i, "text": row[:140], "full": row, "scale": scale, "token": token})
                        break
    return out


def raw_occurrences(texts: dict[str, str], printed: float) -> list[dict[str, Any]]:
    out = []
    for source, raw in texts.items():
        for i, row in enumerate(st.rows(raw)):
            for number, token in st.numbers(row):
                if abs(abs(number) - abs(printed)) <= max(0.005, 1e-7 * abs(printed)):
                    out.append({"source": source, "row": i, "text": row[:140], "full": row, "token": token,
                                "negative": token.startswith("(") and token.endswith(")")})
    return out


def digits(value: float) -> str:
    text = f"{abs(value):.2f}".rstrip("0").rstrip(".")
    return text.replace(".", "")


def digit_distance(a: str, b: str) -> tuple[int, list[tuple[str, str]]]:
    if len(a) == len(b):
        pairs = [(x, y) for x, y in zip(a, b) if x != y]
        return len(pairs), pairs
    if abs(len(a) - len(b)) == 1:
        longer, shorter = (a, b) if len(a) > len(b) else (b, a)
        for i in range(len(longer)):
            if longer[:i] + longer[i + 1:] == shorter:
                return 1, []
    return 99, []


def unit_ratio(got: float, truth: float) -> float | None:
    if not truth or not got:
        return None
    ratio = abs(got / truth)
    for scale in (1e2, 1e3, 1e4, 1e5, 1e6, 1e7, 1e-2, 1e-3, 1e-4, 1e-5, 1e-6, 1e-7):
        if abs(ratio / scale - 1) <= 0.006:
            return scale
    return None


def value_after_label(row: str, field: str, value: float, amount: bool) -> bool:
    rule = st.RULES[field]
    body = st.SERIAL.sub("", row)
    for pattern in rule.include:
        match = pattern.search(body)
        if match and not (rule.exclude is not None and rule.exclude.search(body[: match.end() + 12])):
            tail = st.tail_values(body, match.end(), rule)
            if any(close(v * scale, value, amount) for v in tail[:6] for scale in (SCALES if amount else (1.0,))):
                return True
    return False


def label_snippet(row: str, field: str) -> str:
    for pattern in st.RULES[field].include:
        match = pattern.search(row)
        if match:
            return row[match.start(): match.start() + 110]
    return row[:110]


def field_row(text: str, field: str) -> bool:
    rule = st.RULES[field]
    body = st.SERIAL.sub("", text)
    return any(p.search(body) for p in rule.include) and not (rule.exclude is not None and rule.exclude.search(body[:80]))


def unit_plausible(field: str, value: float | None) -> bool:
    low, high = st.PLAUSIBLE.get(field, (0.0, float("inf")))
    return value is None or value == 0 or low <= abs(value) <= high


def headline_backs_scale(case: dict[str, Any], scale: float) -> bool:
    headline = case.get("headline")
    if headline is None or case["field"] != "net_profit" or case["truth"] is None:
        return False
    return abs(headline - case["truth"] * scale) <= headline_step(headline) and abs(headline - case["truth"]) > headline_step(headline)


def header_units(texts: dict[str, str]) -> dict[str, str | None]:
    return {source: st.header_unit(raw)[1] for source, raw in texts.items()}


def classify(case: dict[str, Any]) -> tuple[str, str]:
    field, method, got, truth = case["field"], case["method"], case["got"], case["truth"]
    printed, texts, label = case["printed"], case["texts"], case["label"]
    amount = field in fq.AMOUNTS
    base = method.removesuffix("_v2")
    own = {base: texts[base]} if base in texts else {}
    units = {u for u in header_units(texts).values() if u}
    language = label.get("language") or case["item"].get("language")
    if truth is None:
        hits = [o for o in occurrences(texts, got, amount) if value_after_label(o["full"], field, got, amount)]
        if hits:
            return "likely_label_error", f"label says not in report; {hits[0]['source']} prints it: {label_snippet(hits[0]['full'], field)}"
        return "wrong_row", "label says not in report; the value read is not on a row for this field"
    truth_hits = raw_occurrences(texts, printed)
    if close(got, -truth, amount) or (method == "headline" and abs(got + truth) <= headline_step(got)):
        signed = [h for h in truth_hits if h["negative"] and field_row(h["full"], field)]
        if signed:
            hit = signed[0]
            return "likely_label_error", f"label is positive; {hit['source']} shows {hit['token']} on: {hit['text']}"
        return "sign", f"{method} {got:,.0f} vs label {truth:,.0f}; the report text shows no bracket or loss marker"
    if method == "headline" and abs(got - truth) <= headline_step(got):
        return "headline_rounding", f"headline {got:,.0f} is printed to {headline_step(got):,.0f}; label {truth:,.0f} is inside that step"
    scale = unit_ratio(got, truth) if amount else None
    if scale is None and amount and method == "headline":
        for s in (1e2, 1e3, 1e4, 1e5, 1e-2, 1e-3, 1e-4, 1e-5):
            if abs(got - truth * s) <= headline_step(got):
                scale = s
                break
    if scale is not None:
        label_unit = label.get("unit") or "rupees"
        if not unit_plausible(field, truth) or headline_backs_scale(case, scale):
            return "likely_label_error", f"label unit {label_unit} gives {truth:,.0f}; {method} {got:,.0f} differs by x{scale:g} and is the plausible or headline-backed figure"
        return "unit_scale", f"{method} differs from the label by x{scale:g}; label unit {label_unit}, header unit {', '.join(sorted(units)) or 'not found'}"
    got_hits = occurrences(texts, got, amount)
    agreeing = [h for h in got_hits if value_after_label(h["full"], field, got, amount)]
    if method == "headline" and agreeing and not any(field_row(h["full"], field) for h in truth_hits):
        where = f"label value is on: {truth_hits[0]['text']}" if truth_hits else "label value is not printed in any text"
        return "likely_label_error", f"headline matches {agreeing[0]['source']} on: {agreeing[0]['text']}; {where}"
    if method != "headline" and case.get("headline") is not None and abs(got - case["headline"]) <= headline_step(case["headline"]) and not truth_hits:
        return "likely_label_error", f"{method} agrees with the headline {case['headline']:,.0f}; the label value {printed:g} is printed in no text"
    if own and not raw_occurrences(own, printed):
        distance, pairs = digit_distance(digits(got / (fq.UNIT.get(label.get('unit') or 'rupees', 1.0) if amount else 1.0)), digits(printed))
        if language != "english" and base == "paddleocr_mobile" and raw_occurrences({k: v for k, v in texts.items() if k == "tesseract"}, printed):
            return "nepali_numeral", "label value is read by Tesseract (nep+eng) but not by the English PaddleOCR model"
        if distance <= 2:
            if pairs and language != "english" and all(frozenset(p) in LOOKALIKE for p in pairs):
                return "nepali_numeral", f"{method} digits differ only in Devanagari look-alike pairs {pairs}"
            return "digit_misread", f"{method} {digits(got)} vs printed {digits(printed)}"
    same_row = [h for h in got_hits if any(h["source"] == t["source"] and h["row"] == t["row"] for t in truth_hits)]
    if same_row:
        source = same_row[0]["source"]
        all_rows = st.rows(texts[source])
        layout = st.layout_near(all_rows, same_row[0]["row"])
        if len(layout.blocks) > 1:
            return "consolidation", f"same row, other entity column ({'/'.join(layout.blocks)}): {same_row[0]['text']}"
        return "period", f"same row, other period column ({'/'.join(layout.periods)}): {same_row[0]['text']}"
    if got_hits:
        hit = got_hits[0]
        if OWNERS.search(hit["text"]):
            return "consolidation", f"{method} took the attributable or non-controlling row: {hit['text']}"
        return "wrong_row", f"{method} value is on: {hit['text']}"
    distance, _ = digit_distance(digits(got), digits(truth))
    if distance <= 2:
        return "digit_misread", f"{method} {digits(got)} vs label {digits(truth)}"
    if not truth_hits:
        return "unexplained", "neither value is printed in any text of this report"
    return "unexplained", f"label value is on: {truth_hits[0]['text']}; {method} value is printed nowhere"


def report_texts(item: dict[str, Any], context: dict[str, Any]) -> dict[str, str]:
    out = {}
    for engine in ("tesseract", "paddleocr_mobile", "surya"):
        path = fq.DERIVED / engine / f"{item['sha256']}.txt"
        if path.exists():
            out[engine] = path.read_text()
    pdf = fq.DERIVED / "text_pdf" / f"{context.get('text_pdf_sha') or item['sha256']}.txt"
    if pdf.exists():
        out["text_pdf"] = pdf.read_text()
    return out


def printed_value(label: dict[str, Any], field: str) -> float | None:
    v = label.get(field)
    return None if v is None or label.get(f"{field}_absent") else float(v)


def label_audit(row: dict[str, Any]) -> list[str]:
    label, item, texts = row["label"], row["item"], row["texts"]
    reasons = []
    headline = item.get("headline_net_profit")
    np_truth = fq.gold_value(label, "net_profit")
    for field in ("net_profit", "net_worth", "reserves", "paid_up_capital"):
        truth = fq.gold_value(label, field)
        if truth is not None and not unit_plausible(field, truth):
            reasons.append(f"unit: {field} {printed_value(label, field):,.0f} x {label.get('unit')} = Rs {truth:,.0f} is outside the plausible range")
    if headline is not None and np_truth:
        for scale in (1e3, 1e-3, 1e2, 1e-2, 1e5, 1e-5):
            if abs(headline - np_truth * scale) <= headline_step(headline) and abs(headline - np_truth) > headline_step(headline):
                reasons.append(f"unit: label net profit Rs {np_truth:,.0f} ({label.get('unit')}); the headline Rs {headline:,.0f} matches it x{scale:g}")
    listed = item.get("quarter")
    if label.get("period") == "quarterly" and listed == listed and listed is not None and str(label.get("quarter")) != str(int(listed)):
        reasons.append(f"quarter: label {label.get('quarter')}, announcement says {int(listed)}")
    if str(label.get("fiscal_year") or "").replace("/0", "/") not in (item.get("fiscal_year") or "").replace("/0", "/") and item.get("fiscal_year"):
        reasons.append(f"fiscal year: label {label.get('fiscal_year')}, announcement {item.get('fiscal_year')}")
    for field in ("net_profit",):
        printed = printed_value(label, field)
        if printed is not None:
            negative = [h for h in raw_occurrences(texts, printed) if h["negative"] and field_row(h["full"], field)]
            if negative and printed > 0:
                reasons.append(f"net_profit sign: printed {negative[0]['token']} on '{negative[0]['text'][:70]}'")
    return reasons


def duplicate_values(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    seen: dict[tuple[str, float], list[tuple[str, str]]] = {}
    for row in rows:
        for field in fq.AMOUNTS:
            printed = printed_value(row["label"], field)
            if printed is not None and len(digits(printed).rstrip("0")) > 3:
                seen.setdefault((field, printed), []).append((row["label"]["label_id"], row["item"]["symbol"]))
    out = []
    for (field, printed), entries in seen.items():
        if len({symbol for _, symbol in entries}) > 1:
            for label_id, symbol in entries:
                others = ", ".join(f"{i} ({s})" for i, s in entries if i != label_id)
                out.append((label_id, f"{field}: the same printed value {printed:,.2f} is labelled for another company on {others}"))
    return out


def run() -> dict[str, Any]:
    rows = fq.labelled_rows()
    cases = []
    table: dict[str, Counter] = {}
    recheck: dict[str, dict[str, Any]] = {}
    for row in rows:
        label, item = row["label"], row["item"]
        texts = report_texts(item, row["context"])
        row["texts"] = texts
        for reason in label_audit(row):
            recheck.setdefault(label["label_id"], {"symbol": item["symbol"], "fiscal_year": item["fiscal_year"], "reasons": []})["reasons"].append(reason)
        for method in CLASSIFIED:
            values = row["methods"].get(method, {})
            for field in fq.FIELDS:
                got = values.get(field)
                truth = fq.gold_value(label, field)
                if got is None or fq.correct(got, truth, field):
                    continue
                case = {"field": field, "method": method, "got": got, "truth": truth, "printed": printed_value(label, field),
                        "texts": {k: v for k, v in texts.items() if k in TEXT_SOURCES}, "label": label, "item": item,
                        "headline": row["context"].get("headline_net_profit") if field == "net_profit" else None}
                cause, evidence = classify(case)
                table.setdefault(f"{method}:{field}", Counter())[cause] += 1
                cases.append({"label_id": label["label_id"], "symbol": item["symbol"], "fiscal_year": item["fiscal_year"], "method": method,
                              "field": field, "got": got, "label": truth, "cause": cause, "evidence": evidence})
                if cause == "likely_label_error":
                    entry = recheck.setdefault(label["label_id"], {"symbol": item["symbol"], "fiscal_year": item["fiscal_year"], "reasons": []})
                    reason = f"{field} ({method}): {evidence}"
                    if reason not in entry["reasons"]:
                        entry["reasons"].append(reason)
    def summary(methods: tuple[str, ...]) -> dict[str, Any]:
        chosen = [c for c in cases if c["method"] in methods]
        return {"methods": list(methods), "mismatches": len(chosen), "by_cause": dict(Counter(c["cause"] for c in chosen)),
                "by_field": {f: dict(Counter(c["cause"] for c in chosen if c["field"] == f)) for f in fq.FIELDS}}

    for label_id, reason in duplicate_values(rows):
        item = next(r["item"] for r in rows if r["label"]["label_id"] == label_id)
        recheck.setdefault(label_id, {"symbol": item["symbol"], "fiscal_year": item["fiscal_year"], "reasons": []})["reasons"].append(reason)
    by_method = {m: dict(Counter(c["cause"] for c in cases if c["method"] == m)) for m in CLASSIFIED}
    kept = [r for r in rows if r["label"]["label_id"] not in recheck]
    sensitivity = fq.measure(kept, fq.METHODS)
    report = {"labelled_reports": len(rows), "before": summary(BEFORE), "after": summary(AFTER), "by_method": by_method,
              "by_method_field": {k: dict(v) for k, v in sorted(table.items())},
              "recheck": dict(sorted(recheck.items())),
              "precision_excluding_recheck": {"reports": len(kept), "note": "sensitivity only; the labels stand until rechecked",
                                              "decisions": fq.decisions(sensitivity),
                                              "results": {m: {f: {k: v[k] for k in ("right", "produced", "errors", "precision")} for f, v in fields.items()}
                                                          for m, fields in sensitivity.items()}},
              "cases": cases}
    REPORT.write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str))
    RECHECK.write_text(recheck_markdown(report["recheck"]))
    return report


def recheck_markdown(recheck: dict[str, dict[str, Any]]) -> str:
    lines = ["# Labels to recheck", "",
             f"{len(recheck)} reports, generated by `venv/bin/python -m src.archive.fundamentals_causes`. Open each id in the label tool "
             "(`venv/bin/python -m src.archive.label_tool`), compare with the image, and save. The evidence is what the report text or the "
             "Sharesansar headline shows; the image decides.", "",
             "| Label | Symbol | Fiscal year | Why |", "| --- | --- | --- | --- |"]
    for label_id, entry in recheck.items():
        reasons = []
        for reason in entry["reasons"]:
            short = reason.split(" (", 1)[0] + ":" + reason.split(":", 1)[1] if " (" in reason.split(":", 1)[0] else reason
            short = short.replace("|", "/")[:170]
            if short not in reasons:
                reasons.append(short)
        lines.append(f"| {label_id} | {entry['symbol']} | {entry['fiscal_year']} | {'<br>'.join(reasons[:3])} |")
    return "\n".join(lines) + "\n"


def print_table(part: dict[str, Any], by_method: dict[str, dict[str, int]]) -> None:
    print(" | ".join(["field"] + list(CAUSES) + ["total"]))
    for field in fq.FIELDS:
        counts = part["by_field"].get(field, {})
        print(" | ".join([field] + [str(counts.get(c, 0)) for c in CAUSES] + [str(sum(counts.values()))]))
    print(" | ".join(["all fields"] + [str(part["by_cause"].get(c, 0)) for c in CAUSES] + [str(part["mismatches"])]))
    for method in part["methods"]:
        counts = by_method.get(method, {})
        print(" | ".join([method] + [str(counts.get(c, 0)) for c in CAUSES] + [str(sum(counts.values()))]))


def main() -> None:
    argparse.ArgumentParser(description="Classify every mismatch between an extraction method and the labels").parse_args()
    report = run()
    print("before (headline and the original readers)")
    print_table(report["before"], report["by_method"])
    print("after (v2 readers)")
    print_table(report["after"], report["by_method"])
    print(len(report["recheck"]), "reports to recheck")
    for label_id, entry in report["recheck"].items():
        print(label_id, entry["symbol"], entry["fiscal_year"], "|", entry["reasons"][0][:150])


if __name__ == "__main__":
    main()
