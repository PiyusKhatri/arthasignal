from __future__ import annotations

import argparse
import io
import json
import logging
import re
from datetime import date, datetime
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.archive import schema
from src.archive.polite import PoliteClient

logger = logging.getLogger(__name__)

SOURCE = "nrb:monthly_statistics"
COLLECTOR = "nrb_monthly_statistics_v1"
LIST_URL = "https://www.nrb.org.np/category/monthly-statistics/page/{page}/?department=bfr"
ENTRY = re.compile(r'<a href="(https://www\.nrb\.org\.np/bfr/[^"]+/)" target="_blank">([^<]+)</a>\s*</span>\s*(?:<span>\s*\((.*?)\)\s*</span>\s*)?</div>\s*'
                   r'<div class="font-size-xs">\s*<span class="mr-3 text-muted">([^<]+)</span>', re.S)
XLSX = re.compile(r'href="([^"]+\.xlsx?)"', re.I)
PERIOD = re.compile(r"\(Mid[\s-]*([A-Za-z]+)[,\s]*(\d{4})\)", re.I)
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
EXTRACTION = "extraction-v2"
MARGIN_LABEL = re.compile(r"margin\s*(nature|type)?\s*loan", re.I)


def entries(client: PoliteClient) -> list[dict[str, Any]]:
    out = []
    page = 1
    while True:
        html = client.get(LIST_URL.format(page=page) if page > 1 else "https://www.nrb.org.np/category/monthly-statistics/?department=bfr").text
        found = ENTRY.findall(html)
        if not found:
            break
        for url, title, files, day in found:
            xlsx = XLSX.search(files or "")
            out.append({"url": url, "title": title.strip(), "xlsx": xlsx.group(1) if xlsx else None,
                        "uploaded": datetime.strptime(day.strip(), "%B %d, %Y").date()})
        page += 1
    return out


def period_end(title: str) -> date | None:
    match = PERIOD.search(title)
    if not match or match.group(1)[:3].lower() not in MONTHS:
        return None
    return date(int(match.group(2)), MONTHS[match.group(1)[:3].lower()], 15)


def margin_rows(payload: bytes) -> list[dict[str, Any]]:
    out = []
    try:
        sheets = pd.read_excel(io.BytesIO(payload), sheet_name=None, header=None)
    except Exception as exc:
        logger.warning("workbook not readable: %s", exc)
        return out
    for name, frame in sheets.items():
        for _, row in frame.iterrows():
            cells = [c for c in row.tolist() if not (isinstance(c, float) and pd.isna(c))]
            labels = [str(c) for c in cells if isinstance(c, str)]
            if not any(MARGIN_LABEL.search(l) for l in labels):
                continue
            numbers = [float(c) for c in cells if isinstance(c, (int, float)) and not isinstance(c, bool)]
            if numbers:
                out.append({"sheet": str(name), "label": " | ".join(labels)[:120], "numbers": numbers[:12]})
    return out


def run(engine: Engine, start: date = date(2013, 7, 1)) -> dict[str, Any]:
    schema.apply(engine)
    client = PoliteClient()
    done = schema.finished(engine, COLLECTOR)
    found = [e for e in entries(client) if e["uploaded"] >= start]
    totals = {"entries": len(found), "workbooks": 0, "with_margin_rows": 0}
    for entry in found:
        if entry["url"] in done:
            continue
        target = entry["xlsx"]
        if not target:
            hop = client.get(entry["url"], allow_redirects=False)
            target = hop.headers.get("location") if hop.status_code in (301, 302) else None
        if not target:
            schema.mark(engine, COLLECTOR, entry["url"], "not_found", 0, "no workbook or pdf")
            continue
        response = client.get(target)
        if response.status_code != 200:
            schema.mark(engine, COLLECTOR, entry["url"], "error", 0, f"http {response.status_code}")
            continue
        entry = {**entry, "xlsx": target}
        suffix = ".pdf" if target.lower().endswith(".pdf") else (".xlsx" if target.lower().endswith("x") else ".xls")
        digest, path = schema.store_raw("nrb_monthly_statistics", response.content, suffix)
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO archive_documents (source, announcement_source_id, symbol, url, sha256, content_type, bytes, path, uploaded_at, published_date) "
                "VALUES (:src, :aid, NULL, :url, :sha, :ct, :b, :p, NULL, :pd) ON CONFLICT (url) DO NOTHING"),
                {"src": SOURCE, "aid": f"{entry['url']}|{entry['title']}", "url": entry["xlsx"], "sha": digest,
                 "ct": "application/pdf" if suffix == ".pdf" else "application/vnd.ms-excel",
                 "b": len(response.content), "p": str(path), "pd": entry["uploaded"]})
        totals["workbooks"] += 1
        schema.mark(engine, COLLECTOR, entry["url"], "done", 1, entry["title"])
        logger.info("%s %s", entry["uploaded"], entry["title"])
    return totals


PDF_MARGIN = re.compile(r"Margin\s+Nature\s+Loans?\s+((?:-?[\d,]+(?:\.\d+)?\s+){5})", re.I)


def margin_from_pdf(payload: bytes) -> tuple[float, str] | None:
    import pypdf

    try:
        reader = pypdf.PdfReader(io.BytesIO(payload))
    except Exception:
        return None
    for number, page in enumerate(reader.pages):
        body = re.sub(r"\s+", " ", page.extract_text() or "")
        match = PDF_MARGIN.search(body)
        if match:
            values = [float(v.replace(",", "")) for v in match.group(1).split()]
            if abs(sum(values[:3]) - values[3]) <= 1.0:
                return values[3], f"pdf page {number + 1}, class A + B + C = total layout, fourth figure (total)"
            return values[4], f"pdf page {number + 1}, first margin row with five figures, fifth figure (rounded to NPR million in the pdf)"
    return None


def margin_outstanding(payload: bytes) -> tuple[float, str] | None:
    if payload[:4] == b"%PDF":
        return margin_from_pdf(payload)
    try:
        sheets = pd.read_excel(io.BytesIO(payload), sheet_name=None, header=None)
    except Exception:
        return None
    for name, frame in sheets.items():
        text_cells = frame.astype(str)
        if not text_cells.apply(lambda col: col.str.contains("Loans and Advances", case=False)).any().any():
            continue
        margin = frame.index[text_cells.apply(lambda r: r.str.contains(r"margin\s*nature", case=False, regex=True).any(), axis=1)]
        change = [(i, j) for i in range(min(8, len(frame))) for j in range(frame.shape[1]) if "% change" in str(frame.iat[i, j]).lower()]
        if margin.empty or not change:
            continue
        header_row, first_change = min(change, key=lambda x: x[1])
        value = frame.iat[margin[0], first_change - 1]
        if isinstance(value, (int, float)) and not pd.isna(value):
            label = f"{frame.iat[header_row, first_change - 1]} {frame.iat[header_row + 1, first_change - 1] if header_row + 1 < len(frame) else ''}"
            return float(value), f"sheet {name}, aggregate block, column {first_change - 1} ({label.strip()})"
    return None


def store_margin(engine: Engine, research: Any) -> dict[str, Any]:
    from src.archive.sentiment_series import _insert

    with research.connect() as connection:
        docs = pd.read_sql(text("SELECT announcement_source_id, url, path, published_date FROM archive_documents WHERE source = :s ORDER BY published_date"),
                           connection, params={"s": SOURCE})
    rows, missing = [], []
    for doc in docs.itertuples():
        title = doc.announcement_source_id.split("|", 1)[-1]
        end = period_end(title)
        with open(doc.path, "rb") as handle:
            found = margin_outstanding(handle.read())
        if end is None or found is None:
            missing.append(title)
            continue
        rows.append({"s": "nrb_margin_loan_outstanding", "ps": None, "pe": end, "v": found[0], "u": "NPR million (BFIs aggregate)", "pd": doc.published_date,
                     "b": "NRB listing upload date of the monthly statistics", "url": f"{doc.url}#{EXTRACTION}", "e": f"{title}: {found[1]}"})
    return {"workbooks": len(docs), "values": len(rows), "added": _insert(engine, rows), "not_parsed": missing[:30]}


def inspect(research: Any) -> list[dict[str, Any]]:
    with research.connect() as connection:
        docs = pd.read_sql(text("SELECT announcement_source_id, path, published_date FROM archive_documents WHERE source = :s ORDER BY published_date"),
                           connection, params={"s": SOURCE})
    out = []
    for doc in docs.itertuples():
        with open(doc.path, "rb") as handle:
            rows = margin_rows(handle.read())
        title = doc.announcement_source_id.split("|", 1)[-1]
        out.append({"title": title, "period_end": str(period_end(title)), "published": str(doc.published_date), "rows": rows[:4]})
    return out


def main() -> None:
    from src.database.connection import engine
    from src.database.holdout_guard import research_engine

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["collect", "inspect", "margin"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.command == "collect":
        print(json.dumps(run(engine), indent=1, default=str))
    elif args.command == "margin":
        print(json.dumps(store_margin(engine, research_engine()), indent=1, default=str))
    else:
        print(json.dumps(inspect(research_engine()), indent=1, default=str)[:6000])


if __name__ == "__main__":
    main()
