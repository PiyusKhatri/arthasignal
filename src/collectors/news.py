from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from src.collectors.mentions import SymbolBook, language
from src.collectors.store import Item

logger = logging.getLogger(__name__)

NPT = ZoneInfo("Asia/Kathmandu")
VERSION = "news-v1"
USER_AGENT = "Mozilla/5.0 (compatible; arthasignal-research/1.0; low-rate news index for internal research)"
DELAY_SECONDS = 3.0
TIMEOUT = 20
MAX_DETAILS_PER_RUN = 40

SHARESANSAR_LIST = "https://www.sharesansar.com/category/latest"
MEROLAGANI_LIST = "https://merolagani.com/NewsList.aspx"
MEROLAGANI_DETAIL = "https://merolagani.com/NewsDetail.aspx?newsID={id}"
ARTHASAROKAR_FEED = "https://arthasarokar.com/feed"
BIZMANDU_FEED = "https://bizmandu.com/feed"
KATHMANDUPOST_FEED = "https://kathmandupost.com/rss"
KATHMANDUPOST_SECTIONS = ("money",)
NS = {"content": "http://purl.org/rss/1.0/modules/content/", "dc": "http://purl.org/dc/elements/1.1/"}


class Http:
    def __init__(self, delay: float = DELAY_SECONDS) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.delay = delay
        self._last = 0.0

    def get(self, url: str) -> requests.Response:
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            try:
                self._last = time.monotonic()
                response = self.session.get(url, timeout=TIMEOUT)
                response.raise_for_status()
                if "charset" not in response.headers.get("Content-Type", "").lower():
                    response.encoding = "utf-8"
                return response
            except requests.RequestException as error:
                logger.warning("GET %s failed (%s), attempt %d", url, type(error).__name__, attempt + 1)
                time.sleep(self.delay * (attempt + 1))
        raise RuntimeError(f"GET {url} failed after retries")


def clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def html_text(fragment: str | None) -> str | None:
    if not fragment:
        return None
    return clean(BeautifulSoup(fragment, "html.parser").get_text(" "))


def parse_npt(value: str, formats: tuple[str, ...]) -> datetime | None:
    value = clean(value) or ""
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=NPT)
        except ValueError:
            continue
    return None


def parse_sharesansar_list(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for block in soup.select("div.featured-news-list"):
        link = block.select_one("a[title]") or block.select_one("a[href*='/newsdetail/']")
        if link is None or "/newsdetail/" not in (link.get("href") or ""):
            continue
        href = link["href"]
        day = block.select_one("span.text-org")
        out.append({"id": href.rstrip("/").split("/newsdetail/")[-1], "url": href,
                    "title": clean(link.get("title") or link.get_text(" ")),
                    "day": parse_npt(day.get_text(" ") if day else "", ("%A, %B %d, %Y",))})
    return out


def parse_sharesansar_detail(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    stamp = None
    for h5 in soup.find_all("h5"):
        if h5.find("i", class_="fa-calendar"):
            match = re.search(r"([A-Z][a-z]{2}, [A-Z][a-z]{2} \d{1,2}, \d{4} \d{1,2}:\d{2} [AP]M)", h5.get_text(" "))
            if match:
                stamp = parse_npt(match.group(1), ("%a, %b %d, %Y %I:%M %p",))
                break
    body = soup.select_one("#newsdetail-content")
    return {"published_at": stamp, "body": clean(body.get_text(" ")) if body else None}


def parse_merolagani_list(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for block in soup.select("div.media-news"):
        link = block.select_one("h4.media-title a[href*='newsID=']")
        if link is None:
            continue
        match = re.search(r"newsID=(\d+)", link["href"])
        if not match or match.group(1) in seen:
            continue
        seen.add(match.group(1))
        label = block.select_one("span.media-label")
        out.append({"id": match.group(1), "url": MEROLAGANI_DETAIL.format(id=match.group(1)), "title": clean(link.get_text(" ")),
                    "published_at": parse_npt(label.get_text(" ") if label else "", ("%b %d, %Y %I:%M %p",))})
    return out


def parse_merolagani_detail(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    parts = [soup.select_one("#ctl00_ContentPlaceHolder1_newsOverview"), soup.select_one("#ctl00_ContentPlaceHolder1_newsDetail")]
    for part in parts:
        if part is not None:
            for ad in part.select(".news-inner-ads, script, style"):
                ad.decompose()
    date = soup.select_one("#ctl00_ContentPlaceHolder1_newsDate")
    source = soup.select_one("#ctl00_ContentPlaceHolder1_newsSource")
    return {"body": clean(" ".join(p.get_text(" ") for p in parts if p is not None)),
            "published_at": parse_npt(date.get_text(" ") if date else "", ("%b %d, %Y %I:%M %p",)),
            "original_source": clean(source.get_text(" ")) if source else None}


def parse_rss(xml: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml)
    out = []
    for item in root.iter("item"):
        def field(tag: str) -> str | None:
            node = item.find(tag, NS)
            return node.text if node is not None else None

        published = field("pubDate")
        try:
            stamp = parsedate_to_datetime(published) if published else None
        except (TypeError, ValueError):
            stamp = None
        link = clean(field("link"))
        out.append({"id": clean(field("guid")) or link, "url": link, "title": clean(field("title")),
                    "body": html_text(field("content:encoded")) or html_text(field("description")),
                    "author": clean(field("dc:creator")), "published_at": stamp,
                    "categories": [clean(c.text) for c in item.findall("category") if c.text]})
    return out


def kathmandupost_day(url: str) -> datetime | None:
    match = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", url or "")
    if not match:
        return None
    return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=NPT)


class Collector:
    source = ""

    def __init__(self, http: Http, book: SymbolBook, known: set[str], max_details: int = MAX_DETAILS_PER_RUN) -> None:
        self.http, self.book, self.known, self.max_details = http, book, known, max_details

    def item(self, entry: dict[str, Any], body: str | None, published: datetime | None, precision: str, **raw: Any) -> Item:
        return Item(source=self.source, source_item_id=str(entry["id"]), url=entry.get("url"), title=entry.get("title"), body=body,
                    published_at=published, published_precision=precision if published else "none", author=entry.get("author"),
                    language=language(entry.get("title"), body), symbols=self.book.find(entry.get("title"), body), raw=raw)

    def collect(self) -> tuple[list[Item], dict[str, Any]]:
        raise NotImplementedError


class SharesansarCollector(Collector):
    source = "sharesansar_news"

    def collect(self) -> tuple[list[Item], dict[str, Any]]:
        entries = parse_sharesansar_list(self.http.get(SHARESANSAR_LIST).text)
        fresh = [e for e in entries if e["id"] not in self.known]
        items, details = [], 0
        for entry in fresh:
            if details >= self.max_details:
                break
            detail = parse_sharesansar_detail(self.http.get(entry["url"]).text)
            details += 1
            if detail["published_at"] is not None:
                items.append(self.item(entry, detail["body"], detail["published_at"], "minute", list_day=entry["day"]))
            else:
                items.append(self.item(entry, detail["body"], entry["day"], "day", list_day=entry["day"]))
        return items, {"listed": len(entries), "new": len(fresh), "details_fetched": details}


class MerolaganiCollector(Collector):
    source = "merolagani_news"

    def collect(self) -> tuple[list[Item], dict[str, Any]]:
        entries = parse_merolagani_list(self.http.get(MEROLAGANI_LIST).text)
        fresh = [e for e in entries if e["id"] not in self.known]
        items, details = [], 0
        for entry in fresh:
            if details >= self.max_details:
                break
            detail = parse_merolagani_detail(self.http.get(entry["url"]).text)
            details += 1
            published = detail["published_at"] or entry["published_at"]
            items.append(self.item(entry, detail["body"], published, "minute", original_source=detail["original_source"]))
        return items, {"listed": len(entries), "new": len(fresh), "details_fetched": details}


class FeedCollector(Collector):
    feed = ""
    sections: tuple[str, ...] = ()

    def accept(self, entry: dict[str, Any]) -> bool:
        return True

    def stamp(self, entry: dict[str, Any]) -> tuple[datetime | None, str]:
        return entry["published_at"], "second"

    def collect(self) -> tuple[list[Item], dict[str, Any]]:
        entries = [e for e in parse_rss(self.http.get(self.feed).text) if e["id"] and self.accept(e)]
        fresh = [e for e in entries if e["id"] not in self.known]
        items = []
        for entry in fresh:
            published, precision = self.stamp(entry)
            items.append(self.item(entry, entry["body"], published, precision, categories=entry["categories"]))
        return items, {"listed": len(entries), "new": len(fresh), "details_fetched": 0}


class ArthasarokarCollector(FeedCollector):
    source = "arthasarokar"
    feed = ARTHASAROKAR_FEED


class BizmanduCollector(FeedCollector):
    source = "bizmandu"
    feed = BIZMANDU_FEED


class KathmanduPostCollector(FeedCollector):
    source = "kathmandupost_money"
    feed = KATHMANDUPOST_FEED

    def accept(self, entry: dict[str, Any]) -> bool:
        return any(f"kathmandupost.com/{s}/" in (entry.get("url") or "") for s in KATHMANDUPOST_SECTIONS)

    def stamp(self, entry: dict[str, Any]) -> tuple[datetime | None, str]:
        if entry["published_at"] is not None:
            return entry["published_at"], "second"
        return kathmandupost_day(entry.get("url") or ""), "day"


COLLECTORS: dict[str, Callable[..., Collector]] = {
    "sharesansar_news": SharesansarCollector,
    "merolagani_news": MerolaganiCollector,
    "arthasarokar": ArthasarokarCollector,
    "bizmandu": BizmanduCollector,
    "kathmandupost_money": KathmanduPostCollector,
}


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)
