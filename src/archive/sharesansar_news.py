from __future__ import annotations

import argparse
import base64
import hashlib
import html as html_lib
import json
import logging
import re
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.archive import schema
from src.archive.polite import PoliteClient

logger = logging.getLogger(__name__)

SOURCE = "sharesansar_news"
COLLECTOR = "sharesansar_news_v1"
LIST_URL = "https://www.sharesansar.com/category/latest"
NEPAL = ZoneInfo("Asia/Kathmandu")
ITEM = re.compile(
    r'<a href="(https://www\.sharesansar\.com/newsdetail/[^"]+)" title="([^"]*)">\s*<h4 class="featured-news-title">.*?</h4>\s*</a>\s*'
    r'<p><span class="text-org">([^<]+)</span>', re.S)
NEXT = re.compile(r'href="\?cursor=([A-Za-z0-9_=-]+)" rel="next"')
STAMP = re.compile(r"([A-Z][a-z]{2}, [A-Z][a-z]{2} \d{1,2}, \d{4} \d{1,2}:\d{2} [AP]M)")
START = datetime(2025, 9, 30, 0, 0, 0)
STOP = date(2014, 1, 1)


def cursor_for(stamp: datetime) -> str:
    return base64.b64encode(json.dumps({"published_date": stamp.strftime("%Y-%m-%d %H:%M:%S"), "_pointsToNextItems": True}).encode()).decode()


def cursor_time(cursor: str) -> datetime:
    padded = cursor + "=" * (-len(cursor) % 4)
    return datetime.strptime(json.loads(base64.b64decode(padded))["published_date"], "%Y-%m-%d %H:%M:%S")


def parse_list(html: str) -> tuple[list[dict[str, Any]], str | None]:
    items = []
    for url, title, day in ITEM.findall(html):
        items.append({"url": url, "title": html_lib.unescape(title), "day": datetime.strptime(day.strip(), "%A, %B %d, %Y").date()})
    nxt = NEXT.search(html)
    return items, nxt.group(1) if nxt else None


def parse_detail(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    body = soup.find(id="newsdetail-content")
    stamp = STAMP.search(html)
    published = datetime.strptime(stamp.group(1), "%a, %b %d, %Y %I:%M %p").replace(tzinfo=NEPAL) if stamp else None
    return {"body": body.get_text("\n", strip=True) if body else None, "published": published}


def known_urls(engine: Engine, urls: list[str]) -> set[str]:
    with engine.connect() as connection:
        return {r[0] for r in connection.execute(text("SELECT url FROM news_articles WHERE source = :s AND url = ANY(:u)"), {"s": SOURCE, "u": urls})}


def store(engine: Engine, item: dict[str, Any], html: bytes, detail: dict[str, Any]) -> int:
    raw_sha, path = schema.store_raw(SOURCE, html, ".html")
    published = detail["published"]
    precision = "minute"
    if published is None or published.astimezone(NEPAL).date() != item["day"]:
        published = datetime.combine(item["day"], datetime.min.time(), tzinfo=NEPAL)
        precision = "day"
    body = detail["body"]
    with engine.begin() as connection:
        return connection.execute(text(
            "INSERT INTO news_articles (source, source_id, url, title, category, published_at, published_precision, body, body_sha256, raw_sha256, raw_path) "
            "VALUES (:src, :sid, :url, :title, 'latest', :pub, :prec, :body, :bsha, :rsha, :path) ON CONFLICT (source, source_id) DO NOTHING"),
            {"src": SOURCE, "sid": item["url"].rsplit("/", 1)[-1], "url": item["url"], "title": item["title"], "pub": published, "prec": precision,
             "body": body, "bsha": hashlib.sha256(body.encode()).hexdigest() if body else None, "rsha": raw_sha, "path": str(path)}).rowcount


def resume_point(engine: Engine, start: datetime) -> datetime:
    with engine.connect() as connection:
        row = connection.execute(text("SELECT detail FROM archive_progress WHERE collector = :c AND item_key = :k"),
                                 {"c": COLLECTOR, "k": f"cursor_from_{start:%Y%m%d}"}).fetchone()
    return datetime.fromisoformat(row[0]) if row and row[0] else start


def run(engine: Engine, start: datetime = START, stop: date = STOP, max_pages: int | None = None) -> dict[str, Any]:
    schema.apply(engine)
    client = PoliteClient()
    position = resume_point(engine, start)
    key = f"cursor_from_{start:%Y%m%d}"
    totals = {"pages": 0, "items": 0, "stored": 0, "skipped_known": 0, "no_body": 0}
    logger.info("starting at %s, stopping before %s", position, stop)
    cursor = cursor_for(position)
    while True:
        page = client.get(LIST_URL, params={"cursor": cursor})
        items, nxt = parse_list(page.text)
        totals["pages"] += 1
        if not items:
            logger.info("empty page at %s", cursor_time(cursor))
            break
        fresh = known_urls(engine, [i["url"] for i in items])
        for item in items:
            totals["items"] += 1
            if item["url"] in fresh:
                totals["skipped_known"] += 1
                continue
            try:
                response = client.get(item["url"])
            except RuntimeError as exc:
                logger.warning("detail failed, recorded for retry: %s", exc)
                schema.mark(engine, COLLECTOR, item["url"], "error", 0, str(exc))
                totals["failed"] = totals.get("failed", 0) + 1
                continue
            if response.status_code != 200:
                logger.warning("detail %s http %d", item["url"], response.status_code)
                continue
            detail = parse_detail(response.text)
            totals["no_body"] += detail["body"] is None
            totals["stored"] += store(engine, item, response.content, detail)
        if nxt is None:
            break
        position = cursor_time(nxt)
        schema.mark(engine, COLLECTOR, key, "running", 0, position.isoformat())
        logger.info("page %d done, next %s, totals %s", totals["pages"], position, totals)
        if position.date() < stop or (max_pages and totals["pages"] >= max_pages):
            break
        cursor = nxt
    schema.mark(engine, COLLECTOR, key, "done" if position.date() < stop else "running", 0, position.isoformat())
    return totals


def main() -> None:
    from src.database.connection import engine

    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=datetime.fromisoformat, default=START)
    parser.add_argument("--stop", type=date.fromisoformat, default=STOP)
    parser.add_argument("--max-pages", type=int)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(run(engine, args.start, args.stop, args.max_pages), indent=1))


if __name__ == "__main__":
    main()
