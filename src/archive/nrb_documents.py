from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.archive import schema
from src.archive.polite import PoliteClient

logger = logging.getLogger(__name__)

SOURCE = "nrb"
COLLECTOR = "nrb_documents_v1"
MONTHS = ("one-month", "two-months", "three-months", "four-months", "five-months", "six-months", "seven-months", "eight-months",
          "nine-months", "ten-months", "eleven-months", "twelve-months")
CATEGORIES = {
    "monetary_policy": ["https://www.nrb.org.np/category/monetary-policy/"],
    "macro_situation": [f"https://www.nrb.org.np/category/current-macroeconomic-situation/?department=red&fy={y}-{str(y + 1)[-2:]}&subcategory={m}"
                        for y in range(2070, 2082) for m in MONTHS],
}
ENTRY = re.compile(r'<a href="(https://www\.nrb\.org\.np/[a-z]+/[^"]+/)" target="_blank">([^<]+)</a>\s*</span>\s*</div>\s*<div class="font-size-xs">\s*'
                   r'<span class="mr-3 text-muted">([^<]+)</span>', re.S)
PAGE = re.compile(r'href="(https://www\.nrb\.org\.np/category/[^"]+/page/(\d+)/)"')
PDF = re.compile(r'href="(https://www\.nrb\.org\.np/contents/uploads/[^"]+\.pdf)"', re.I)


def _page_url(base: str, number: int) -> str:
    if "?" in base:
        path, query = base.split("?", 1)
        return f"{path}page/{number}/?{query}"
    return f"{base}page/{number}/"


def listing(client: PoliteClient, base: str) -> list[dict[str, Any]]:
    first = client.get(base)
    pages = sorted({int(n) for _, n in PAGE.findall(first.text)} | {1})
    entries = []
    for number in range(1, max(pages) + 1):
        html = first.text if number == 1 else client.get(_page_url(base, number)).text
        for url, title, day in ENTRY.findall(html):
            entries.append({"url": url, "title": re.sub(r"\s+", " ", title).strip(), "uploaded": datetime.strptime(day.strip(), "%B %d, %Y").date()})
    return entries


def collect(engine: Engine, client: PoliteClient, category: str, entry: dict[str, Any]) -> dict[str, Any]:
    page = client.get(entry["url"], allow_redirects=False)
    location = page.headers.get("location") or ""
    if page.status_code in (301, 302) and location.lower().endswith(".pdf"):
        links = [location]
    elif page.status_code == 200:
        links = list(dict.fromkeys(PDF.findall(page.text)))
    else:
        return {"status": "not_found", "rows": 0, "detail": f"http {page.status_code}"}
    added = 0
    for url in links:
        response = client.get(url)
        if response.status_code != 200:
            continue
        digest, path = schema.store_raw("nrb", response.content, ".pdf")
        with engine.begin() as connection:
            added += connection.execute(text(
                "INSERT INTO archive_documents (source, announcement_source_id, symbol, url, sha256, content_type, bytes, path, uploaded_at, published_date) "
                "VALUES (:src, :aid, NULL, :url, :sha, 'application/pdf', :b, :p, NULL, :pd) ON CONFLICT (url) DO NOTHING"),
                {"src": f"{SOURCE}:{category}", "aid": f"{entry['url']}|{entry['title']}", "url": url, "sha": digest, "b": len(response.content),
                 "p": str(path), "pd": entry["uploaded"]}).rowcount
    return {"status": "done", "rows": added, "detail": f"{len(links)} pdf"}


def run(engine: Engine, categories: list[str], english_only: bool = False) -> dict[str, Any]:
    schema.apply(engine)
    client = PoliteClient()
    done = schema.finished(engine, COLLECTOR)
    totals = {"entries": 0, "documents": 0, "errors": 0}
    for category in categories:
        found = []
        for base in CATEGORIES[category]:
            try:
                found.extend(listing(client, base))
            except RuntimeError as exc:
                logger.warning("listing failed, skipped: %s", exc)
                totals["errors"] += 1
        entries = list({e["url"]: e for e in found}.values())
        if english_only:
            entries = [e for e in entries if "english" in e["title"].lower()]
        for entry in entries:
            totals["entries"] += 1
            if entry["url"] in done:
                continue
            try:
                result = collect(engine, client, category, entry)
            except Exception as exc:
                logger.exception("%s failed", entry["url"])
                schema.mark(engine, COLLECTOR, entry["url"], "error", 0, str(exc))
                totals["errors"] += 1
                continue
            schema.mark(engine, COLLECTOR, entry["url"], result["status"], result["rows"], f"{entry['title']} | {result['detail']}")
            totals["documents"] += result["rows"]
            logger.info("%s %s %s", entry["uploaded"], entry["title"], result["detail"])
    return totals


def main() -> None:
    from src.database.connection import engine

    parser = argparse.ArgumentParser()
    parser.add_argument("--categories", nargs="*", default=list(CATEGORIES))
    parser.add_argument("--english-only", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(run(engine, args.categories, args.english_only), indent=1))


if __name__ == "__main__":
    main()
