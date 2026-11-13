"""Telegram public channels via the official MTProto API (Telethon).

Needs TELEGRAM_API_ID / TELEGRAM_API_HASH (my.telegram.org) and a session string,
created once with:  python -m ioi.cli telegram-login
"""
import asyncio

from .. import config
from ..db import iso


def _check():
    if not (config.TELEGRAM_API_ID and config.TELEGRAM_API_HASH and config.TELEGRAM_SESSION):
        from . import SourceNotConfigured

        raise SourceNotConfigured("TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_SESSION missing")


def _client():
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    return TelegramClient(
        StringSession(config.TELEGRAM_SESSION), int(config.TELEGRAM_API_ID), config.TELEGRAM_API_HASH
    )


async def _resolve(account):
    from telethon.tl.functions.channels import GetFullChannelRequest

    async with _client() as client:
        entity = await client.get_entity(account["handle"].lstrip("@"))
        full = await client(GetFullChannelRequest(entity))
        return {"platform_user_id": str(entity.id), "followers": full.full_chat.participants_count}


async def _fetch(account):
    async with _client() as client:
        handle = account["handle"].lstrip("@")
        entity = await client.get_entity(handle)
        min_id = int(account.get("last_seen_id") or 0)
        limit = 50 if min_id else 5
        posts = []
        async for msg in client.iter_messages(entity, min_id=min_id, limit=limit):
            if not msg.message:
                continue
            posts.append(
                {
                    "id": f"telegram:{handle}:{msg.id}",
                    "native_id": msg.id,
                    "account_key": account["key"],
                    "platform": "telegram",
                    "url": f"https://t.me/{handle}/{msg.id}",
                    "text": msg.message,
                    "created_at": iso(msg.date),
                    "likes": sum(r.count for r in msg.reactions.results) if msg.reactions else 0,
                    "replies": msg.replies.replies if msg.replies else 0,
                    "reposts": msg.forwards or 0,
                    "views": msg.views or 0,
                }
            )
        return posts


def resolve(account: dict) -> dict:
    _check()
    return asyncio.run(_resolve(account))


def fetch(account: dict) -> list[dict]:
    _check()
    return asyncio.run(_fetch(account))


def newest_id(posts: list[dict]) -> str | None:
    ids = [int(p["native_id"]) for p in posts]
    return str(max(ids)) if ids else None


def login():
    """Interactive, run once: prints a session string for TELEGRAM_SESSION."""
    from telethon.sync import TelegramClient
    from telethon.sessions import StringSession

    api_id = config.TELEGRAM_API_ID or input("TELEGRAM_API_ID: ")
    api_hash = config.TELEGRAM_API_HASH or input("TELEGRAM_API_HASH: ")
    with TelegramClient(StringSession(), int(api_id), api_hash) as client:
        print("\nTELEGRAM_SESSION (keep this secret, store it as a GitHub secret):\n")
        print(client.session.save())
