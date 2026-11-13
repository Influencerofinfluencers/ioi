"""Telegram bot: status messages to you, digest to the free channel, alerts to Pro."""
import html
import json
import logging
import re

import httpx

from . import config

log = logging.getLogger("ioi.notify")
LIMIT = 3900  # Telegram max is 4096 characters per message


def md_to_telegram(md: str) -> str:
    """Small Markdown -> Telegram-HTML converter (headings, bold, italics, links, bullets)."""
    out = []
    for line in md.splitlines():
        s = html.escape(line, quote=False)
        s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", s)
        if re.match(r"^#{1,6} ", s):
            s = "<b>" + s.lstrip("#").strip() + "</b>"
        elif re.match(r"^\s*[-*] ", s):
            s = "• " + re.sub(r"^\s*[-*] ", "", s)
        out.append(s)
    return "\n".join(out).strip()


def _chunks(text: str):
    while text:
        if len(text) <= LIMIT:
            yield text
            return
        cut = text.rfind("\n", 0, LIMIT)
        cut = cut if cut > 0 else LIMIT
        yield text[:cut]
        text = text[cut:].lstrip("\n")


def send(chat_id: str, text: str, is_html: bool = True, buttons: list | None = None) -> bool:
    if not (config.TELEGRAM_BOT_TOKEN and chat_id):
        log.info("telegram not configured, message:\n%s", text[:500])
        return False
    ok = True
    for part in _chunks(text):
        r = httpx.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": part,
                "parse_mode": "HTML" if is_html else None,
                "disable_web_page_preview": True,
                **({"reply_markup": {"inline_keyboard": buttons}} if buttons else {}),
            },
            timeout=30,
        )
        if r.status_code != 200:
            log.warning("telegram send failed: %s", r.text[:300])
            ok = False
    return ok


def photo(chat_id: str, image, caption: str) -> bool:
    if not (config.TELEGRAM_BOT_TOKEN and chat_id):
        return False
    try:
        with open(image, "rb") as fh:
            r = httpx.post(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendPhoto",
                           data={"chat_id": chat_id, "caption": caption[:1000], "parse_mode": "HTML"},
                           files={"photo": ("card.png", fh, "image/png")}, timeout=60)
        return r.status_code == 200
    except Exception as e:  # noqa: BLE001
        log.warning("telegram photo failed: %s", e)
        return False


def status(text: str) -> bool:
    return send(config.TELEGRAM_STATUS_CHAT_ID, html.escape(text, quote=False))


def digest(md: str) -> bool:
    return send(config.TELEGRAM_PUBLIC_CHAT_ID, md_to_telegram(md))


def alert(a: dict) -> bool:
    text = f"⚡ <b>{html.escape(a['topic'])}</b>\n{html.escape(a['message'])}\n<a href=\"{a['url']}\">First post</a>"
    return send(config.TELEGRAM_PRO_CHAT_ID, text)


def pro_teasers() -> int:
    """Free channel gets a delayed teaser of Pro alerts: proof that Pro is faster."""
    from . import db

    if not (config.PRO_CHECKOUT_URL and config.TELEGRAM_PUBLIC_CHAT_ID):
        return 0
    done_today = db.rows("SELECT COUNT(*) AS n FROM alerts WHERE teased_at >= :s", s=db.ago(hours=24))[0]["n"]
    room = config.TEASERS_PER_DAY - done_today
    if room <= 0:
        return 0
    due = db.rows(
        """SELECT * FROM alerts WHERE teased_at IS NULL AND created_at <= :late AND created_at >= :early
           ORDER BY created_at LIMIT :n""",
        late=db.ago(hours=config.TEASER_DELAY_HOURS), early=db.ago(hours=24), n=room,
    )
    sent = 0
    for a in due:
        n = len(json.loads(a["accounts"] or "[]"))
        text = (
            f"⚡ <b>Pro members got this {config.TEASER_DELAY_HOURS}h ago</b> "
            f"({a['created_at'][11:16]} UTC)\n"
            f"<b>{html.escape(a['topic'])}</b>: {n} tracked accounts suddenly started talking about it.\n\n"
            f'Get alerts the moment they happen: <a href="{config.PRO_CHECKOUT_URL}">IOI Pro</a>'
        )
        if send(config.TELEGRAM_PUBLIC_CHAT_ID, text):
            db.run("UPDATE alerts SET teased_at = :t WHERE id = :id", t=db.iso(db.now()), id=a["id"])
            sent += 1
    return sent
