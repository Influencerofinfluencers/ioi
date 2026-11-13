"""Bluesky public AppView API - free, no key needed."""
from datetime import datetime

import httpx

from ..db import iso

API = "https://public.api.bsky.app/xrpc"


def resolve(account: dict) -> dict:
    r = httpx.get(f"{API}/app.bsky.actor.getProfile", params={"actor": account["handle"]}, timeout=30)
    r.raise_for_status()
    p = r.json()
    return {"platform_user_id": p["did"], "followers": p.get("followersCount")}


def fetch(account: dict) -> list[dict]:
    r = httpx.get(
        f"{API}/app.bsky.feed.getAuthorFeed",
        params={"actor": account["handle"], "filter": "posts_no_replies", "limit": 30},
        timeout=30,
    )
    r.raise_for_status()
    last = account.get("last_seen_id") or ""
    posts = []
    for item in r.json().get("feed", []):
        if item.get("reason"):  # a repost, not their own words
            continue
        post = item["post"]
        record = post.get("record", {})
        created = iso(datetime.fromisoformat(record["createdAt"].replace("Z", "+00:00")))
        if last and created <= last:
            continue
        rkey = post["uri"].rsplit("/", 1)[-1]
        posts.append(
            {
                "id": f"bluesky:{post['uri']}",
                "native_id": created,
                "account_key": account["key"],
                "platform": "bluesky",
                "url": f"https://bsky.app/profile/{account['handle']}/post/{rkey}",
                "text": record.get("text", ""),
                "created_at": created,
                "likes": post.get("likeCount", 0),
                "replies": post.get("replyCount", 0),
                "reposts": post.get("repostCount", 0) + post.get("quoteCount", 0),
                "views": 0,
            }
        )
    return posts


def newest_id(posts: list[dict]) -> str | None:
    return max((p["native_id"] for p in posts), default=None)
