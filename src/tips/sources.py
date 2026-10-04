from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import robotparser
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from src.collectors import social
from src.collectors.mentions import SymbolBook, language
from src.collectors.news import USER_AGENT, clean
from src.collectors.store import PERMANENT, THIRTY_DAYS, Item

VERSION = "tips-v1"
CONFIG_PATH = Path(os.environ.get("ARTHASIGNAL_TIP_CONFIG", "config/tip_sources.json"))
PLATFORMS = ("youtube", "web", "telegram", "other")
WEB_DELAY_SECONDS = 3.0
TELEGRAM_REASON = (
    "Telegram's Content Licensing and AI Scraping Terms prohibit scraping, indexing, harvesting or aggregation of platform data "
    "beyond ordinary use; Telegram tips may only be entered manually with a recorded permission reference from the channel"
)


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"youtube_channels": [], "web_pages": []}
    return json.loads(path.read_text())


def youtube_videos(book: SymbolBook, channels: list[dict[str, str]], key: str | None = None) -> tuple[list[Item], dict[str, Any]]:
    key = key or social.require_env("YOUTUBE_API_KEY")["YOUTUBE_API_KEY"]
    session = requests.Session()
    quota = {"units": 0}
    items: list[Item] = []
    detail: dict[str, Any] = {}
    for channel in channels:
        info = social.youtube_get(session, "channels", {"part": "contentDetails,snippet", "id": channel["channel_id"]}, key, quota)
        if not info.get("items"):
            detail[channel["channel_id"]] = {"error": "channel not found"}
            continue
        public_name = info["items"][0]["snippet"]["title"]
        uploads = info["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        videos = social.youtube_get(session, "playlistItems", {"part": "snippet", "playlistId": uploads, "maxResults": social.YOUTUBE_RECENT_VIDEOS},
                                    key, quota)
        for video in videos.get("items", []):
            snippet = video["snippet"]
            video_id = snippet["resourceId"]["videoId"]
            items.append(Item(
                source="tip_youtube_video", channel=public_name, source_item_id=video_id, url=f"https://www.youtube.com/watch?v={video_id}",
                title=snippet.get("title"), body=snippet.get("description"),
                published_at=datetime.fromisoformat(snippet["publishedAt"].replace("Z", "+00:00")), published_precision="second",
                language=language(snippet.get("title"), snippet.get("description")),
                symbols=book.find(snippet.get("title"), snippet.get("description")), retention=THIRTY_DAYS,
                raw={"platform": "youtube", "channel_id": channel["channel_id"]},
            ))
        detail[public_name] = {"videos": len(videos.get("items", []))}
    detail["quota_units"] = quota["units"]
    return items, detail


def robots_allows(url: str, fetch: Any = None) -> bool:
    parts = urlparse(url)
    parser = robotparser.RobotFileParser()
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        response = (fetch or requests.get)(robots_url, headers={"User-Agent": USER_AGENT}, timeout=20)
    except requests.RequestException:
        return False
    if response.status_code >= 500:
        return False
    if response.status_code >= 400:
        return True
    parser.parse(response.text.splitlines())
    return parser.can_fetch(USER_AGENT, url)


def page_text(html: str) -> tuple[str | None, str | None]:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "nav", "header", "footer", "form", "noscript"]):
        node.decompose()
    title = clean(soup.title.get_text(" ")) if soup.title else None
    lines = [clean(line) for line in soup.get_text("\n").splitlines()]
    return title, "\n".join(line for line in lines if line) or None


def web_pages(book: SymbolBook, pages: list[dict[str, str]], fetch: Any = None) -> tuple[list[Item], dict[str, Any]]:
    fetch = fetch or requests.get
    items: list[Item] = []
    detail: dict[str, Any] = {}
    for page in pages:
        url = page["url"]
        if "t.me/" in url or "telegram." in url:
            detail[page["name"]] = {"skipped": TELEGRAM_REASON}
            continue
        if not robots_allows(url, fetch):
            detail[page["name"]] = {"skipped": "robots.txt disallows or is unreachable"}
            continue
        time.sleep(WEB_DELAY_SECONDS)
        response = fetch(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        if response.status_code != 200:
            detail[page["name"]] = {"status": response.status_code}
            continue
        if "charset" not in response.headers.get("Content-Type", "").lower():
            response.encoding = "utf-8"
        title, body = page_text(response.text)
        items.append(Item(
            source="tip_web_page", channel=page["name"], source_item_id=url, url=url, title=title, body=body, published_at=None,
            published_precision="none", language=language(title, body), symbols=book.find(title, body), retention=PERMANENT,
            raw={"platform": "web"},
        ))
        detail[page["name"]] = {"chars": len(body or "")}
    return items, detail


def manual_item(book: SymbolBook, platform: str, channel: str, url: str, posted_at: datetime, text_value: str,
                consent_ref: str | None = None) -> Item:
    if platform not in PLATFORMS:
        raise ValueError(f"platform must be one of {PLATFORMS}")
    if platform == "telegram" and not consent_ref:
        raise PermissionError(TELEGRAM_REASON)
    if posted_at.tzinfo is None:
        raise ValueError("posted_at needs a timezone")
    if not re.match(r"^https?://", url or ""):
        raise ValueError("a public URL is required")
    return Item(
        source="tip_manual", channel=channel, source_item_id=f"{url}#{posted_at.astimezone(timezone.utc).isoformat()}", url=url, title=None,
        body=text_value, published_at=posted_at, published_precision="minute", language=language(text_value),
        symbols=book.find(text_value), retention=THIRTY_DAYS if platform == "youtube" else PERMANENT,
        raw={"platform": platform, "consent_ref": consent_ref},
    )


def platform_of(source: str, raw: dict[str, Any]) -> str:
    if source == "tip_youtube_video":
        return "youtube"
    if source == "tip_web_page":
        return "web"
    return raw.get("platform", "other")
