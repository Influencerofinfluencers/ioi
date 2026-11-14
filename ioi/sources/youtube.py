"""YouTube: free RSS feed per channel. The Data API key is only used once per
channel, to turn the @handle into a channel id and read the subscriber count."""
import json
import re
from calendar import timegm
from datetime import datetime, timedelta, timezone

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


PAGE = "https://www.youtube.com/channel/{cid}/videos"
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
           "Accept-Language": "en-US,en;q=0.9"}
UNITS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800, "month": 2592000, "year": 31536000}


def _ago(text: str) -> int | None:
    m = re.search(r"(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago", text or "")
    return int(m.group(1)) * UNITS[m.group(2)] if m else None


def _count(text: str) -> int:
    m = re.match(r"([\d.,]+)\s*([KMB]?)", (text or "").replace(",", ""))
    if not m:
        return 0
    return int(float(m.group(1)) * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2)])


def parse_videos_page(html: str) -> list[dict]:
    """The channel's /videos page (ytInitialData, lockupViewModel layout): id, title, age in seconds, views."""
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", html, re.S)
    if not m:
        return []
    found = []

    def walk(o):
        if isinstance(o, dict):
            if "lockupViewModel" in o:
                found.append(o["lockupViewModel"])
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(json.loads(m.group(1)))
    out = []
    for l in found:
        if l.get("contentType") != "LOCKUP_CONTENT_TYPE_VIDEO" or not l.get("contentId"):
            continue
        meta = (l.get("metadata") or {}).get("lockupMetadataViewModel") or {}
        parts = [p.get("text", {}).get("content", "") for row in
                 (((meta.get("metadata") or {}).get("contentMetadataViewModel") or {}).get("metadataRows") or [])
                 for p in row.get("metadataParts") or []]
        age = next((a for a in map(_ago, parts) if a is not None), None)
        views = next((_count(p) for p in parts if "view" in p.lower()), 0)
        out.append({"vid": l["contentId"], "title": (meta.get("title") or {}).get("content", ""),
                    "age": age, "views": views})
    return out


def _fetch_page(account: dict, last: str) -> list[dict]:
    """Fallback when the RSS feed is blocked for cloud IPs (seen since Nov 2026)."""
    from .. import db

    r = httpx.get(PAGE.format(cid=account["platform_user_id"]), headers=BROWSER, timeout=30, follow_redirects=True)
    r.raise_for_status()
    vids = parse_videos_page(r.text)
    if not vids:
        raise ValueError("YouTube videos page: no videos parsed (layout changed?)")
    known = {x["id"] for x in db.rows("SELECT id FROM posts WHERE account_key = :a AND platform = 'youtube'",
                                      a=account["key"])}
    now = datetime.now(timezone.utc)
    posts = []
    for v in vids:
        pid = f"youtube:{v['vid']}"
        if pid in known or v["age"] is None or v["age"] > 7 * 86400:
            continue
        created = iso(now - timedelta(seconds=v["age"]))
        posts.append({"id": pid, "native_id": max(created, last) if last else created,
                      "account_key": account["key"], "platform": "youtube",
                      "url": f"https://www.youtube.com/watch?v={v['vid']}", "text": v["title"],
                      "created_at": created, "likes": 0, "replies": 0, "reposts": 0, "views": v["views"]})
    return posts


def fetch(account: dict) -> list[dict]:
    if not account.get("platform_user_id"):
        account.update(resolve(account))
    last = account.get("last_seen_id") or ""
    r = httpx.get(FEED.format(cid=account["platform_user_id"]), timeout=30, headers=BROWSER)
    if r.status_code >= 400:  # RSS blocked/flaky for cloud IPs: use the normal videos page
        return _fetch_page(account, last)
    feed = feedparser.parse(r.text)
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
