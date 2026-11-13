"""X (Twitter) API v2, pay-per-use.

Cost control:
* only original posts (exclude=replies,retweets), so fewer billable results;
* since_id = last post we stored, so each post is paid for once;
* the very first run per account fetches only 5 posts.
"""
import httpx

from .. import config
from ..db import iso
from datetime import datetime


def _client():
    if not config.X_BEARER_TOKEN:
        from . import SourceNotConfigured

        raise SourceNotConfigured("X_BEARER_TOKEN missing")
    return httpx.Client(
        base_url=config.X_API_BASE,
        headers={"Authorization": f"Bearer {config.X_BEARER_TOKEN}"},
        timeout=30,
    )


def resolve_many(accounts: list[dict]) -> dict[str, dict]:
    """One request for up to 100 usernames (cheaper than one call per account)."""
    out = {}
    handles = [a["handle"].lstrip("@") for a in accounts]
    with _client() as c:
        for i in range(0, len(handles), 100):
            chunk = handles[i : i + 100]
            r = c.get(
                "/users/by",
                params={"usernames": ",".join(chunk), "user.fields": "public_metrics"},
            )
            r.raise_for_status()
            for u in r.json().get("data", []):
                out[u["username"].lower()] = {
                    "platform_user_id": u["id"],
                    "followers": u.get("public_metrics", {}).get("followers_count"),
                }
    return out


def resolve(account: dict) -> dict:
    res = resolve_many([account]).get(account["handle"].lstrip("@").lower())
    if not res:
        raise ValueError(f"X user not found: {account['handle']}")
    return res


def fetch(account: dict) -> list[dict]:
    if not account.get("platform_user_id"):
        account.update(resolve(account))
    params = {
        "exclude": "replies,retweets",
        "tweet.fields": "created_at,public_metrics",
        "max_results": max(5, min(100, config.X_MAX_RESULTS)),
    }
    if account.get("last_seen_id"):
        params["since_id"] = account["last_seen_id"]
    else:
        params["max_results"] = 5  # first run: small sample only
    with _client() as c:
        r = c.get(f"/users/{account['platform_user_id']}/tweets", params=params)
        r.raise_for_status()
        data = r.json().get("data", [])
    handle = account["handle"].lstrip("@")
    posts = []
    for t in data:
        m = t.get("public_metrics", {})
        created = datetime.fromisoformat(t["created_at"].replace("Z", "+00:00"))
        posts.append(
            {
                "id": f"x:{t['id']}",
                "native_id": t["id"],
                "account_key": account["key"],
                "platform": "x",
                "url": f"https://x.com/{handle}/status/{t['id']}",
                "text": t.get("text", ""),
                "created_at": iso(created),
                "likes": m.get("like_count", 0),
                "replies": m.get("reply_count", 0),
                "reposts": m.get("retweet_count", 0) + m.get("quote_count", 0),
                "views": m.get("impression_count", 0),
            }
        )
    return posts


def newest_id(posts: list[dict]) -> str | None:
    ids = [int(p["native_id"]) for p in posts]
    return str(max(ids)) if ids else None
