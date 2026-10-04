from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from src.collectors.mentions import SymbolBook, language
from src.collectors.store import PERMANENT, THIRTY_DAYS, Item

logger = logging.getLogger(__name__)

VERSION = "social-v1"
CONFIG_PATH = Path(os.environ.get("ARTHASIGNAL_SOCIAL_CONFIG", "config/social_sources.json"))
TELEGRAM_FIRST_RUN_LIMIT = 500
TELEGRAM_RUN_LIMIT = 1000
YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
YOUTUBE_RECENT_VIDEOS = 10
YOUTUBE_VIDEO_MAX_AGE_DAYS = 14
YOUTUBE_COMMENT_PAGES = 3
REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_API = "https://oauth.reddit.com"
REDDIT_DELAY_SECONDS = 1.0


class NotConfigured(RuntimeError):
    pass


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"telegram_channels": [], "youtube_channel_ids": [], "reddit_subreddits": []}
    return json.loads(path.read_text())


def require_env(*names: str) -> dict[str, str]:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise NotConfigured(f"missing environment variables: {', '.join(missing)}")
    return {n: os.environ[n] for n in names}


def telegram_item(channel: str, channel_id: int, message: Any, book: SymbolBook) -> Item | None:
    body = getattr(message, "message", None) or None
    if body is None and not getattr(message, "media", None):
        return None
    sender = getattr(message, "post_author", None) or getattr(message, "sender_id", None)
    replies = getattr(message, "replies", None)
    return Item(
        source="telegram", channel=channel, source_item_id=f"{channel_id}:{message.id}", url=f"https://t.me/{channel}/{message.id}",
        title=None, body=body, published_at=message.date, published_precision="second", author=str(sender) if sender else None,
        language=language(body), symbols=book.find(body), retention=PERMANENT,
        raw={"views": getattr(message, "views", None), "forwards": getattr(message, "forwards", None),
             "replies": getattr(replies, "replies", None) if replies else None,
             "edit_date": getattr(message, "edit_date", None), "has_media": bool(getattr(message, "media", None)),
             "forwarded": getattr(message, "fwd_from", None) is not None,
             "terms": "Telegram API terms: no use in developing or deploying ML models"},
    )


def collect_telegram(book: SymbolBook, last_ids: dict[str, int], config: dict[str, Any]) -> tuple[list[Item], dict[str, Any]]:
    if os.environ.get("ARTHASIGNAL_TELEGRAM_TERMS_ACK") != "1":
        raise NotConfigured("Telegram collection is off until ARTHASIGNAL_TELEGRAM_TERMS_ACK=1 is set (see docs/LIVE_COLLECTORS.md)")
    env = require_env("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION")
    channels = config.get("telegram_channels") or []
    if not channels:
        raise NotConfigured("no telegram_channels in the social sources config")
    from telethon import TelegramClient

    async def fetch() -> tuple[list[Item], dict[str, Any]]:
        items: list[Item] = []
        detail: dict[str, Any] = {}
        async with TelegramClient(env["TELEGRAM_SESSION"], int(env["TELEGRAM_API_ID"]), env["TELEGRAM_API_HASH"]) as client:
            for channel in channels:
                entity = await client.get_entity(channel)
                min_id = last_ids.get(channel, 0)
                limit = TELEGRAM_RUN_LIMIT if min_id else TELEGRAM_FIRST_RUN_LIMIT
                count = 0
                async for message in client.iter_messages(entity, min_id=min_id, limit=limit):
                    item = telegram_item(channel, entity.id, message, book)
                    if item is not None:
                        items.append(item)
                        count += 1
                detail[channel] = {"messages": count, "after_message_id": min_id}
        return items, detail

    return asyncio.run(fetch())


def youtube_get(session: requests.Session, path: str, params: dict[str, Any], key: str, quota: dict[str, int]) -> dict[str, Any]:
    response = session.get(f"{YOUTUBE_API}/{path}", params={**params, "key": key}, timeout=20)
    quota["units"] += 1
    response.raise_for_status()
    return response.json()


def youtube_comment_item(video_id: str, thread: dict[str, Any], book: SymbolBook) -> Item:
    top = thread["snippet"]["topLevelComment"]
    snippet = top["snippet"]
    body = snippet.get("textOriginal") or snippet.get("textDisplay")
    published = datetime.fromisoformat(snippet["publishedAt"].replace("Z", "+00:00"))
    return Item(
        source="youtube_comment", channel=video_id, source_item_id=top["id"], url=f"https://www.youtube.com/watch?v={video_id}&lc={top['id']}",
        title=None, body=body, published_at=published, published_precision="second",
        author=(snippet.get("authorChannelId") or {}).get("value"), language=language(body), symbols=book.find(body),
        retention=THIRTY_DAYS,
        raw={"like_count": snippet.get("likeCount"), "reply_count": thread["snippet"].get("totalReplyCount"),
             "updated_at": snippet.get("updatedAt"), "video_id": video_id},
    )


def collect_youtube(book: SymbolBook, config: dict[str, Any], now: datetime | None = None) -> tuple[list[Item], dict[str, Any]]:
    key = require_env("YOUTUBE_API_KEY")["YOUTUBE_API_KEY"]
    channels = config.get("youtube_channel_ids") or []
    if not channels:
        raise NotConfigured("no youtube_channel_ids in the social sources config")
    now = now or datetime.now(tz=timezone.utc)
    session = requests.Session()
    quota = {"units": 0}
    items: list[Item] = []
    detail: dict[str, Any] = {}
    for channel_id in channels:
        info = youtube_get(session, "channels", {"part": "contentDetails", "id": channel_id}, key, quota)
        if not info.get("items"):
            detail[channel_id] = {"error": "channel not found"}
            continue
        uploads = info["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        videos = youtube_get(session, "playlistItems", {"part": "snippet", "playlistId": uploads, "maxResults": YOUTUBE_RECENT_VIDEOS}, key, quota)
        fetched = 0
        for video in videos.get("items", []):
            snippet = video["snippet"]
            video_id = snippet["resourceId"]["videoId"]
            published = datetime.fromisoformat(snippet["publishedAt"].replace("Z", "+00:00"))
            items.append(Item(
                source="youtube_video", channel=channel_id, source_item_id=video_id, url=f"https://www.youtube.com/watch?v={video_id}",
                title=snippet.get("title"), body=snippet.get("description"), published_at=published, published_precision="second",
                author=channel_id, language=language(snippet.get("title"), snippet.get("description")),
                symbols=book.find(snippet.get("title"), snippet.get("description")), retention=THIRTY_DAYS, raw={"channel_id": channel_id},
            ))
            if now - published > timedelta(days=YOUTUBE_VIDEO_MAX_AGE_DAYS):
                continue
            token = None
            for _ in range(YOUTUBE_COMMENT_PAGES):
                params = {"part": "snippet", "videoId": video_id, "maxResults": 100, "order": "time", "textFormat": "plainText"}
                if token:
                    params["pageToken"] = token
                try:
                    page = youtube_get(session, "commentThreads", params, key, quota)
                except requests.HTTPError as error:
                    detail.setdefault("comment_errors", []).append(f"{video_id}: {error.response.status_code if error.response is not None else ''}")
                    break
                for thread in page.get("items", []):
                    items.append(youtube_comment_item(video_id, thread, book))
                    fetched += 1
                token = page.get("nextPageToken")
                if not token:
                    break
        detail[channel_id] = {"videos": len(videos.get("items", [])), "comments": fetched}
    detail["quota_units"] = quota["units"]
    return items, detail


def reddit_token(env: dict[str, str]) -> str:
    response = requests.post(REDDIT_TOKEN_URL, auth=(env["REDDIT_CLIENT_ID"], env["REDDIT_CLIENT_SECRET"]),
                             data={"grant_type": "client_credentials"}, headers={"User-Agent": env["REDDIT_USER_AGENT"]}, timeout=20)
    response.raise_for_status()
    return response.json()["access_token"]


def reddit_item(kind: str, data: dict[str, Any], book: SymbolBook) -> Item:
    title = data.get("title")
    body = data.get("selftext") if kind == "post" else data.get("body")
    return Item(
        source=f"reddit_{kind}", channel=data.get("subreddit", ""), source_item_id=data["name"],
        url=f"https://www.reddit.com{data.get('permalink', '')}", title=title, body=body or None,
        published_at=datetime.fromtimestamp(float(data["created_utc"]), tz=timezone.utc), published_precision="second",
        author=data.get("author_fullname") or data.get("author"), language=language(title, body), symbols=book.find(title, body),
        retention=THIRTY_DAYS,
        raw={"score": data.get("score"), "num_comments": data.get("num_comments"), "link_id": data.get("link_id"),
             "edited": data.get("edited"), "flair": data.get("link_flair_text")},
    )


def collect_reddit(book: SymbolBook, config: dict[str, Any]) -> tuple[list[Item], dict[str, Any]]:
    env = require_env("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT")
    subreddits = config.get("reddit_subreddits") or []
    if not subreddits:
        raise NotConfigured("no reddit_subreddits in the social sources config")
    token = reddit_token(env)
    headers = {"Authorization": f"bearer {token}", "User-Agent": env["REDDIT_USER_AGENT"]}
    items: list[Item] = []
    detail: dict[str, Any] = {}
    for subreddit in subreddits:
        counts = {}
        for kind, path in (("post", "new"), ("comment", "comments")):
            time.sleep(REDDIT_DELAY_SECONDS)
            response = requests.get(f"{REDDIT_API}/r/{subreddit}/{path}", params={"limit": 100}, headers=headers, timeout=20)
            response.raise_for_status()
            children = response.json().get("data", {}).get("children", [])
            for child in children:
                items.append(reddit_item(kind, child["data"], book))
            counts[kind] = len(children)
        detail[subreddit] = counts
    return items, detail
