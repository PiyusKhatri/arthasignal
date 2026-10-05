from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.archive import schema
from src.archive.polite import PoliteClient

logger = logging.getLogger(__name__)

SOURCE = "merolagani_news"
COLLECTOR = "merolagani_news_v1"
DETAIL = "https://merolagani.com/NewsDetail.aspx?newsID={id}"
NEPAL = ZoneInfo("Asia/Kathmandu")
START_ID = 119999
STOP_ID = 12000


def parse(html: str) -> dict[str, Any] | None:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one("#ctl00_ContentPlaceHolder1_newsTitle")
    stamp = soup.select_one("#ctl00_ContentPlaceHolder1_newsDate")
    raw_date = stamp.get_text(" ").strip() if stamp else ""
    if not title or not title.get_text(strip=True) or not raw_date:
        return None
    published, precision = None, None
    for fmt, kind in (("%b %d, %Y %I:%M %p", "minute"), ("%b %d, %Y", "day")):
        try:
            published = datetime.strptime(raw_date, fmt).replace(tzinfo=NEPAL)
            precision = kind
            break
        except ValueError:
            continue
    if published is None:
        return None
    parts = [soup.select_one("#ctl00_ContentPlaceHolder1_newsOverview"), soup.select_one("#ctl00_ContentPlaceHolder1_newsDetail")]
    for part in parts:
        if part is not None:
            for junk in part.select(".news-inner-ads, script, style"):
                junk.decompose()
    body = "\n".join(p.get_text("\n", strip=True) for p in parts if p is not None).strip() or None
    source = soup.select_one("#ctl00_ContentPlaceHolder1_newsSource")
    return {"title": title.get_text(" ", strip=True), "published": published, "precision": precision, "body": body,
            "original_source": source.get_text(" ", strip=True) if source else None}


def position(engine: Engine) -> int:
    with engine.connect() as connection:
        row = connection.execute(text("SELECT detail FROM archive_progress WHERE collector = :c AND item_key = 'next_id'"), {"c": COLLECTOR}).fetchone()
    return int(row[0]) if row and row[0] else START_ID


def run(engine: Engine, start: int | None = None, stop: int = STOP_ID) -> dict[str, Any]:
    schema.apply(engine)
    client = PoliteClient()
    current = start if start is not None else position(engine)
    totals = {"visited": 0, "stored": 0, "empty": 0, "failed": 0}
    while current >= stop:
        url = DETAIL.format(id=current)
        try:
            response = client.get(url)
        except RuntimeError as exc:
            schema.mark(engine, COLLECTOR, str(current), "error", 0, str(exc))
            totals["failed"] += 1
            current -= 1
            continue
        totals["visited"] += 1
        html = response.content.decode("utf-8", "replace")
        item = parse(html) if response.status_code == 200 else None
        if item is None:
            totals["empty"] += 1
        else:
            raw_sha, path = schema.store_raw(SOURCE, response.content, ".html")
            body = item["body"]
            with engine.begin() as connection:
                totals["stored"] += connection.execute(text(
                    "INSERT INTO news_articles (source, source_id, url, title, category, published_at, published_precision, body, body_sha256, raw_sha256, raw_path) "
                    "VALUES (:src, :sid, :url, :title, :cat, :pub, :prec, :body, :bsha, :rsha, :path) ON CONFLICT (source, source_id) DO NOTHING"),
                    {"src": SOURCE, "sid": str(current), "url": url, "title": item["title"], "cat": item["original_source"], "pub": item["published"],
                     "prec": item["precision"], "body": body, "bsha": hashlib.sha256(body.encode()).hexdigest() if body else None,
                     "rsha": raw_sha, "path": str(path)}).rowcount
        current -= 1
        if totals["visited"] % 50 == 0:
            schema.mark(engine, COLLECTOR, "next_id", "running", 0, str(current))
            logger.info("next id %d, totals %s", current, totals)
    schema.mark(engine, COLLECTOR, "next_id", "done", 0, str(current))
    return totals


def main() -> None:
    from src.database.connection import engine

    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int)
    parser.add_argument("--stop", type=int, default=STOP_ID)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(run(engine, args.start, args.stop), indent=1))


if __name__ == "__main__":
    main()
