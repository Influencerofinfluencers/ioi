"""Instagram via the official Graph API Business Discovery endpoint.

Works for public Business/Creator accounts. Requires your own Instagram
professional account linked to a Facebook Page (IG_USER_ID + IG_ACCESS_TOKEN).
Rate limit: ~200 calls/hour, which is plenty for a handful of accounts.
"""
from datetime import datetime

import httpx

from .. import config
from ..db import iso

FIELDS = (
    "business_discovery.username({u}){{followers_count,media_count,"
    "media.limit({n}){{id,caption,timestamp,permalink,like_count,comments_count}}}}"
)


def _get(username: str, n: int = 10) -> dict:
    if not (config.IG_USER_ID and config.IG_ACCESS_TOKEN):
        from . import SourceNotConfigured

        raise SourceNotConfigured("IG_USER_ID / IG_ACCESS_TOKEN missing")
    r = httpx.get(
        f"https://graph.facebook.com/{config.GRAPH_VERSION}/{config.IG_USER_ID}",
        params={"fields": FIELDS.format(u=username, n=n), "access_token": config.IG_ACCESS_TOKEN},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["business_discovery"]


def resolve(account: dict) -> dict:
    bd = _get(account["handle"].lstrip("@"), n=1)
    return {"platform_user_id": bd.get("id", account["handle"]), "followers": bd.get("followers_count")}


def fetch(account: dict) -> list[dict]:
    bd = _get(account["handle"].lstrip("@"))
    account["followers"] = bd.get("followers_count")
    last = account.get("last_seen_id") or ""
    posts = []
    for m in bd.get("media", {}).get("data", []):
        created = iso(datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z"))
        if last and created <= last:
            continue
        posts.append(
            {
                "id": f"instagram:{m['id']}",
                "native_id": created,
                "account_key": account["key"],
                "platform": "instagram",
                "url": m.get("permalink", ""),
                "text": m.get("caption", "") or "",
                "created_at": created,
                "likes": m.get("like_count", 0) or 0,
                "replies": m.get("comments_count", 0) or 0,
                "reposts": 0,
                "views": 0,
            }
        )
    return posts


def newest_id(posts: list[dict]) -> str | None:
    return max((p["native_id"] for p in posts), default=None)
