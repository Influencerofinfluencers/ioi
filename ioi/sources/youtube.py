"""YouTube: free RSS feed per channel. The Data API key is only used once per
channel, to turn the @handle into a channel id and read the subscriber count."""
from calendar import timegm
from datetime import datetime, timezone

import feedparser
import httpx

from .. import config
from ..db import iso

FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"


def resolve(account: dict) -> dict:
    handle = account["handle"]
    if handle.startswith("UC") and len(handle) == 24:  # already a channel id
        return {"platform_user_id": handle, "followers": None}
    if not config.YOUTUBE_API_KEY:
        from . import SourceNotConfigured

        raise SourceNotConfigured("YOUTUBE_API_KEY missing (needed once to resolve @handles)")
    r = httpx.get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={
            "part": "id,statistics",
            "forHandle": handle if handle.startswith("@") else f"@{handle}",
            "key": config.YOUTUBE_API_KEY,
        },
        timeout=30,
    )
    r.raise_for_status()
    items = r.json().get("items", [])
    if not items:
        raise ValueError(f"YouTube channel not found: {handle}")
    stats = items[0].get("statistics", {})
    return {
        "platform_user_id": items[0]["id"],
        "followers": int(stats["subscriberCount"]) if "subscriberCount" in stats else None,
    }


def fetch(account: dict) -> list[dict]:
    if not account.get("platform_user_id"):
        account.update(resolve(account))
    r = httpx.get(FEED.format(cid=account["platform_user_id"]), timeout=30)
    r.raise_for_status()
    feed = feedparser.parse(r.text)
    last = account.get("last_seen_id") or ""
    posts = []
    for e in feed.entries:
        published = datetime.fromtimestamp(timegm(e.published_parsed), tz=timezone.utc)
        created = iso(published)
        if last and created <= last:  # for YouTube we keep the newest timestamp as cursor
            continue
        vid = getattr(e, "yt_videoid", e.get("id", ""))
        stats = e.get("media_statistics", {}) or {}
        rating = e.get("media_starrating", {}) or {}
        posts.append(
            {
                "id": f"youtube:{vid}",
                "native_id": created,
                "account_key": account["key"],
                "platform": "youtube",
                "url": e.get("link", f"https://www.youtube.com/watch?v={vid}"),
                "text": f"{e.get('title', '')}\n\n{e.get('summary', '')[:1500]}",
                "created_at": created,
                "likes": int(rating.get("count", 0) or 0),
                "replies": 0,
                "reposts": 0,
                "views": int(stats.get("views", 0) or 0),
            }
        )
    return posts


def newest_id(posts: list[dict]) -> str | None:
    return max((p["native_id"] for p in posts), default=None)
