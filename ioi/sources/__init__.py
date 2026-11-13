"""Source adapters. Each adapter exposes:

    resolve(account) -> dict(platform_user_id=..., followers=...)   # identity + reach
    fetch(account)   -> list[post dict]                              # only NEW posts

A post dict has: id, account_key, platform, url, text, created_at (ISO UTC),
likes, replies, reposts, views. Adapters only use official / public APIs.
"""
from . import bluesky, instagram, telegram, x, youtube

ADAPTERS = {
    "x": x,
    "youtube": youtube,
    "telegram": telegram,
    "instagram": instagram,
    "bluesky": bluesky,
}


class SourceNotConfigured(Exception):
    """Raised when the API keys for a platform are missing."""
