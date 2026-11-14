"""E-mail edition via Kit (free Newsletter plan: up to 10,000 subscribers, API key access).
The daily digest that goes to Telegram is also sent as a Kit broadcast. Only active when
KIT_API_KEY is set (GitHub secret). Nothing is sent twice: one broadcast per day (kv guard)."""
import html
import logging
from datetime import timedelta

import httpx
import markdown as md_lib

from . import config, db

log = logging.getLogger("ioi.kit")
API = "https://api.kit.com/v4"


def enabled() -> bool:
    return bool(config.KIT_API_KEY)


def _headers():
    return {"X-Kit-Api-Key": config.KIT_API_KEY, "Accept": "application/json", "Content-Type": "application/json"}


def email_html(title: str, markdown_text: str) -> str:
    body = md_lib.markdown(markdown_text, extensions=["extra"])
    site = config.SITE_URL + "/"
    pro = (f'<p style="margin:24px 0"><a href="{html.escape(config.PRO_CHECKOUT_URL)}" '
           f'style="background:#C8FF3D;color:#0E1116;padding:12px 18px;border-radius:999px;text-decoration:none;'
           f'font-weight:700">Get live alerts with IOI Pro</a></p>') if config.PRO_CHECKOUT_URL else ""
    return (f'<div style="font-family:Inter,Arial,sans-serif;color:#0E1116;line-height:1.55;max-width:620px">'
            f'<p style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#5b6270">'
            f'IOI · Influencer of Influencers · daily briefing</p>'
            f'{body}{pro}'
            f'<p style="font-size:13px;color:#5b6270">Live data, Top 50 and archive: <a href="{site}">{site}</a><br>'
            f'Attention data, not financial advice.</p></div>')


def send_daily(title: str, markdown_text: str, preview: str = "") -> dict:
    """Schedule today's digest as a Kit broadcast to all subscribers (a few minutes from now)."""
    from .pro import _kv

    if not enabled():
        return {"skipped": "KIT_API_KEY not set"}
    today = db.iso(db.now())[:10]
    if _kv("kit_sent_day") == today:
        return {"skipped": "already sent today"}
    now = db.now()
    body = {
        "subject": title[:150],
        "preview_text": (preview or "What crypto's 50 most influential voices said, and who said it first.")[:150],
        "description": f"IOI daily {today}",
        "content": email_html(title, markdown_text),
        "public": True,  # also appears on the Kit web archive (extra discoverability)
        "published_at": db.iso(now),
        "send_at": db.iso(now + timedelta(minutes=3)),
        "email_template_id": int(config.KIT_TEMPLATE_ID) if config.KIT_TEMPLATE_ID else None,
        "email_address": None,
        "thumbnail_alt": None,
        "thumbnail_url": None,
        # every subscriber: Kit treats an empty filter group as "all subscribers"
        "subscriber_filter": [{"all": [], "any": None, "none": None}],
    }
    body = {k: v for k, v in body.items() if not (k == "email_template_id" and v is None)}
    r = httpx.post(f"{API}/broadcasts", json=body, headers=_headers(), timeout=30)
    if r.status_code == 422:  # older accounts reject the empty filter: send without one
        body.pop("subscriber_filter")
        r = httpx.post(f"{API}/broadcasts", json=body, headers=_headers(), timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Kit {r.status_code}: {r.text[:200]}")
    b = r.json().get("broadcast", {})
    _kv("kit_sent_day", today)
    return {"id": b.get("id"), "status": b.get("status"), "send_at": b.get("send_at")}


def check() -> dict:
    """Read-only: account + subscriber count (for verify)."""
    if not enabled():
        return {"skipped": "KIT_API_KEY not set"}
    acc = httpx.get(f"{API}/account", headers=_headers(), timeout=20)
    subs = httpx.get(f"{API}/subscribers", params={"per_page": 1, "include_total_count": "true"},
                     headers=_headers(), timeout=20)
    return {"account": acc.status_code, "name": (acc.json().get("account") or {}).get("name") if acc.status_code == 200 else acc.text[:120],
            "subscribers": (subs.json().get("pagination") or {}).get("total_count") if subs.status_code == 200 else subs.status_code}
