from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.archive import schema
from src.archive.polite import PoliteClient

logger = logging.getLogger(__name__)

SOURCE = "sharesansar"
COLLECTOR = "sharesansar_report_documents_v1"
ATTACHMENT = re.compile(r'(?:src|href)="(https://content\.sharesansar\.com/(?:photos/shares/announcement/[^"]+\.(?:jpe?g|png|gif|pdf)|[^"]+\.pdf))"', re.I)
UPLOAD_STAMP = re.compile(r"/(\d{10})-[^/]*$")
SKIP = re.compile(r"/site/|advertis|logo|icon|Facebook Template", re.I)
SUFFIX = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "application/pdf": ".pdf"}


def attachments(html: str) -> list[str]:
    seen: list[str] = []
    for url in ATTACHMENT.findall(html):
        if SKIP.search(url) or url in seen:
            continue
        seen.append(url)
    return seen


def upload_time(url: str) -> datetime | None:
    match = UPLOAD_STAMP.search(url)
    return datetime.fromtimestamp(int(match.group(1)), tz=timezone.utc) if match else None


BROKEN_PREFIX = "https://content.sharesansar.com/photos/shares/announcement//"


def repaired(url: str) -> str:
    if url.startswith(BROKEN_PREFIX):
        return "https://content.sharesansar.com/" + url[len(BROKEN_PREFIX):]
    return url


def _safe(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, quote(parts.path), parts.query, parts.fragment))


def targets(engine: Engine, start: date | None, end: date | None, sample_per_year: int | None, seed: float = 0.2026) -> list[dict[str, Any]]:
    where = "q.source = 'sharesansar' AND q.source_url LIKE 'https://www.sharesansar.com/announcementdetail/%' AND NOT q.is_correction"
    params: dict[str, Any] = {}
    if start:
        where += " AND q.published_date >= :s"
        params["s"] = start
    if end:
        where += " AND q.published_date <= :e"
        params["e"] = end
    with engine.connect() as connection:
        if sample_per_year:
            connection.execute(text("SELECT setseed(:x)"), {"x": seed})
            sql = (f"SELECT source_id, source_url, symbol, fiscal_year, quarter, published_date, net_profit FROM ("
                   f"SELECT q.*, row_number() OVER (PARTITION BY extract(year FROM q.published_date) ORDER BY random()) AS rn "
                   f"FROM quarterly_report_announcements q WHERE {where} AND q.net_profit IS NOT NULL) x WHERE rn <= :n ORDER BY published_date")
            params["n"] = sample_per_year
        else:
            sql = f"SELECT source_id, source_url, symbol, fiscal_year, quarter, published_date, net_profit FROM quarterly_report_announcements q WHERE {where} ORDER BY published_date DESC"
        return [dict(r._mapping) for r in connection.execute(text(sql), params)]


def collect(engine: Engine, client: PoliteClient, target: dict[str, Any]) -> dict[str, Any]:
    page = client.get(target["source_url"])
    if page.status_code != 200:
        return {"status": "not_found", "rows": 0, "detail": f"http {page.status_code}"}
    urls = attachments(page.text)
    added = 0
    kinds = []
    for url in urls:
        response = client.get(_safe(url))
        if response.status_code != 200 and repaired(url) != url:
            url = repaired(url)
            response = client.get(_safe(url))
        if response.status_code != 200:
            kinds.append(f"http{response.status_code}")
            continue
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        suffix = SUFFIX.get(content_type) or "." + url.rsplit(".", 1)[-1].lower()
        digest, path = schema.store_raw("sharesansar_reports", response.content, suffix)
        kinds.append(content_type)
        with engine.begin() as connection:
            added += connection.execute(text(
                "INSERT INTO archive_documents (source, announcement_source_id, symbol, url, sha256, content_type, bytes, path, uploaded_at, published_date) "
                "VALUES (:src, :aid, :sym, :url, :sha, :ct, :b, :p, :up, :pd) ON CONFLICT (url) DO NOTHING"),
                {"src": SOURCE, "aid": target["source_id"], "sym": target["symbol"], "url": url, "sha": digest, "ct": content_type,
                 "b": len(response.content), "p": str(path), "up": upload_time(url), "pd": target["published_date"]}).rowcount
    return {"status": "done", "rows": added, "detail": ",".join(kinds) or "no attachment"}


def run(engine: Engine, start: date | None = None, end: date | None = None, sample_per_year: int | None = None) -> dict[str, Any]:
    schema.apply(engine)
    done = schema.finished(engine, COLLECTOR)
    todo = [t for t in targets(engine, start, end, sample_per_year) if t["source_url"] not in done]
    client = PoliteClient()
    totals = {"done": 0, "not_found": 0, "error": 0, "documents": 0, "without_attachment": 0}
    logger.info("%d report announcements to visit", len(todo))
    for number, target in enumerate(todo, 1):
        try:
            result = collect(engine, client, target)
        except Exception as exc:
            logger.exception("%s failed", target["source_url"])
            schema.mark(engine, COLLECTOR, target["source_url"], "error", 0, str(exc))
            totals["error"] += 1
            continue
        schema.mark(engine, COLLECTOR, target["source_url"], result["status"], result["rows"], result["detail"])
        totals[result["status"]] += 1
        totals["documents"] += result["rows"]
        totals["without_attachment"] += result["detail"] == "no attachment"
        logger.info("%d/%d %s %s %s %s", number, len(todo), target["symbol"], target["published_date"], result["status"], result["detail"])
    return totals


def main() -> None:
    from src.database.connection import engine

    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat, default=date(2025, 9, 29))
    parser.add_argument("--sample-per-year", type=int)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(run(engine, args.start, args.end, args.sample_per_year), indent=1))


if __name__ == "__main__":
    main()
