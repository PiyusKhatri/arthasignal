from __future__ import annotations

import argparse
import io
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.archive import schema
from src.archive.polite import PoliteClient, RobotsDisallowed

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DERIVED = Path("~/Desktop/arthasignal-ai/derived").expanduser()
WEBSITES = ROOT / "docs" / "company_websites.json"
INDEX = DERIVED / "company_pdfs" / "index.json"
TEXT_DIR = DERIVED / "ocr" / "text_pdf"
WEBSITE = re.compile(r'Website Link</td>\s*<td[^>]*>\s*<a[^>]+href="([^"]+)"', re.S)
PAGE_HINT = re.compile(r"report|financial|investor|quarter|interim|download|publication|disclosure|notice|statement", re.I)
PDF_HINT = re.compile(r"quarter|interim|unaudited|q[1-4]\b|1st|2nd|3rd|4th|first|second|third|fourth|ashwin|asoj|poush|push|chait|ashad|asar|financial", re.I)
MAX_PAGES = 12
MAX_PDFS = 120
MONTH_QUARTER = {"ashwin": 1, "asoj": 1, "aswin": 1, "poush": 2, "push": 2, "pous": 2, "chaitra": 3, "chait": 3, "ashadh": 4, "ashad": 4, "asar": 4, "asadh": 4}
QUARTER_WORD = re.compile(r"\b(1st|2nd|3rd|4th|first|second|third|fourth)\s+quarter|\bq([1-4])\b", re.I)
FISCAL = re.compile(r"\b(20[6-8]\d)\s*[/-]\s*(\d{2,4})\b")
MONTH = re.compile(r"\b(" + "|".join(MONTH_QUARTER) + r")\w*\s*(?:end\w*\s*)?,?\s*(20[6-8]\d)", re.I)


def websites(engine: Engine, client: PoliteClient) -> dict[str, str]:
    known = json.loads(WEBSITES.read_text()) if WEBSITES.exists() else {}
    with engine.connect() as connection:
        symbols = [r[0] for r in connection.execute(text(
            "SELECT symbol FROM companies WHERE instrument_type = 'Equity' AND status = 'A' AND trim(symbol) <> '' ORDER BY symbol"))]
    for symbol in symbols:
        if symbol in known:
            continue
        try:
            page = client.get(f"https://www.sharesansar.com/company/{symbol.lower()}")
        except RuntimeError as exc:
            logger.warning("%s website lookup failed: %s", symbol, exc)
            continue
        match = WEBSITE.search(page.text)
        url = match.group(1).strip() if match else ""
        if url.startswith("//"):
            url = "https:" + url
        known[symbol] = url
        WEBSITES.write_text(json.dumps(known, indent=1, sort_keys=True))
    return known


def pdf_text(payload: bytes) -> str:
    import pypdf

    try:
        return "\n".join(page.extract_text() or "" for page in pypdf.PdfReader(io.BytesIO(payload)).pages[:6])
    except Exception:
        return ""


def period(raw: str) -> tuple[str | None, int | None]:
    fiscal = FISCAL.search(raw)
    fy = f"{fiscal.group(1)}/{int(fiscal.group(1)) + 1}" if fiscal and str(int(fiscal.group(1)) + 1)[-2:] == fiscal.group(2)[-2:] else None
    quarter = None
    word = QUARTER_WORD.search(raw)
    if word:
        token = (word.group(1) or word.group(2)).lower()
        quarter = {"1st": 1, "first": 1, "2nd": 2, "second": 2, "3rd": 3, "third": 3, "4th": 4, "fourth": 4}.get(token) or int(token)
    month = MONTH.search(raw)
    if quarter is None and month:
        quarter = MONTH_QUARTER[month.group(1).lower()]
    if fy is None and month:
        year = int(month.group(2))
        quarter_found = MONTH_QUARTER[month.group(1).lower()]
        start = year if quarter_found in (1, 2) else year - 1
        fy = f"{start}/{start + 1}"
    return fy, quarter


def crawl(symbol: str, site: str) -> dict[str, Any]:
    client = PoliteClient()
    result: dict[str, Any] = {"symbol": symbol, "site": site, "pages": 0, "pdf_links": 0, "pdfs": [], "error": None}
    if not site:
        result["error"] = "no website listed"
        return result
    try:
        home = client.get(site)
        host = urlparse(home.url).netloc
        frontier = [home.url]
        pages = {}
        links = re.findall(r'href="([^"#]+)"[^>]*>(.*?)</a>', home.text, re.S | re.I)
        for href, label in links:
            target = urljoin(home.url, href)
            if urlparse(target).netloc == host and (PAGE_HINT.search(href) or PAGE_HINT.search(label)) and target not in frontier:
                frontier.append(target)
        pdfs: dict[str, str] = {}
        for url in frontier[: MAX_PAGES + 1]:
            try:
                page = home if url == home.url else client.get(url)
            except (RuntimeError, RobotsDisallowed):
                continue
            pages[url] = page.status_code
            for href, label in re.findall(r'href="([^"#]+\.pdf[^"]*)"[^>]*>(.*?)</a>', page.text, re.S | re.I):
                target = urljoin(page.url, href)
                if PDF_HINT.search(href) or PDF_HINT.search(re.sub(r"<[^>]+>", " ", label)):
                    pdfs.setdefault(target, re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", label)).strip())
        result["pages"] = len(pages)
        result["pdf_links"] = len(pdfs)
        TEXT_DIR.mkdir(parents=True, exist_ok=True)
        for url, label in list(pdfs.items())[:MAX_PDFS]:
            try:
                response = client.get(url)
            except (RuntimeError, RobotsDisallowed):
                continue
            if response.status_code != 200 or not response.content.startswith(b"%PDF"):
                continue
            digest, path = schema.store_raw("company_reports", response.content, ".pdf")
            body = pdf_text(response.content)
            has_text = len(re.sub(r"\s+", "", body)) > 400
            if has_text:
                (TEXT_DIR / f"{digest}.txt").write_text(body)
            fy, quarter = period(f"{label}\n{url}\n{body[:3000]}")
            result["pdfs"].append({"url": url, "label": label, "sha256": digest, "path": str(path), "bytes": len(response.content),
                                   "text_layer": has_text, "fiscal_year": fy, "quarter": quarter})
    except Exception as exc:
        result["error"] = str(exc)[:200]
    return result


def run(engine: Engine, workers: int = 6) -> dict[str, Any]:
    client = PoliteClient()
    sites = websites(engine, client)
    index = json.loads(INDEX.read_text()) if INDEX.exists() else {}
    todo = [(s, u) for s, u in sorted(sites.items()) if s not in index]
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(lambda item: crawl(*item), todo):
            index[result["symbol"]] = result
            INDEX.write_text(json.dumps(index, indent=1))
            logger.info("%s %s pages=%d pdfs=%d text=%d error=%s", result["symbol"], result["site"], result["pages"], len(result["pdfs"]),
                        sum(p["text_layer"] for p in result["pdfs"]), result["error"])
    return summary(engine, index)


def summary(engine: Engine, index: dict[str, Any]) -> dict[str, Any]:
    pdfs = [dict(p, symbol=r["symbol"]) for r in index.values() for p in r["pdfs"]]
    text_pdfs = [p for p in pdfs if p["text_layer"]]
    with engine.connect() as connection:
        announcements = {(r[0], r[1], r[2]): r[3] for r in connection.execute(text(
            "SELECT symbol, fiscal_year, quarter, min(published_date) FROM quarterly_report_announcements WHERE source = 'sharesansar' "
            "AND NOT is_correction AND symbol IS NOT NULL GROUP BY 1, 2, 3"))}
    matched = []
    for p in text_pdfs:
        if p["fiscal_year"] and p["quarter"]:
            day = announcements.get((p["symbol"], p["fiscal_year"], p["quarter"]))
            if day:
                matched.append({**p, "published_date": str(day)})
    by_year: dict[str, int] = {}
    for p in matched:
        by_year[p["published_date"][:4]] = by_year.get(p["published_date"][:4], 0) + 1
    return {"companies": len(index), "with_website": sum(1 for r in index.values() if r["site"]),
            "sites_with_report_pdfs": sum(1 for r in index.values() if r["pdfs"]), "pdfs": len(pdfs), "text_layer_pdfs": len(text_pdfs),
            "symbols_with_text_pdfs": len({p["symbol"] for p in text_pdfs}), "matched_to_dated_announcement": len(matched),
            "matched_by_publication_year": dict(sorted(by_year.items())), "errors": sum(1 for r in index.values() if r["error"])}


def main() -> None:
    from src.database.connection import engine

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["crawl", "summary"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.command == "crawl":
        out = run(engine)
    else:
        out = summary(engine, json.loads(INDEX.read_text()))
    (ROOT / "docs" / "company_report_pdfs.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
