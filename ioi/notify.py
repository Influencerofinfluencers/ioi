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
    if chat_id and chat_id == config.TELEGRAM_PRO_CHAT_ID:
        _mark_pro_activity()
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
        from datetime import datetime

        age_h = int((db.now() - datetime.fromisoformat(a["created_at"].replace("Z", "+00:00"))).total_seconds() // 3600)
        text = (
            f"⚡ <b>Pro members got this {max(age_h, config.TEASER_DELAY_HOURS)}h ago</b> "
            f"({a['created_at'][11:16]} UTC)\n"
            f"<b>{html.escape(a['topic'])}</b>: {n} tracked accounts suddenly started talking about it.\n\n"
            f'Get alerts the moment they happen: <a href="{config.PRO_CHECKOUT_URL}">IOI Pro</a>'
        )
        if send(config.TELEGRAM_PUBLIC_CHAT_ID, text):
            db.run("UPDATE alerts SET teased_at = :t WHERE id = :id", t=db.iso(db.now()), id=a["id"])
            sent += 1
    return sent


def _mark_pro_activity():
    try:
        from . import db
        from .pro import _kv

        _kv("pro_last_msg", db.iso(db.now()))
    except Exception:  # noqa: BLE001 - bookkeeping must never block a message
        pass


PULSE_GAP_HOURS = 3
PULSE_HOURS_UTC = range(7, 22)  # 08:00-22:59 Amsterdam (winter time)


def pro_pulse(force: bool = False) -> bool:
    """Keeps the Pro feed alive in quiet hours: when no alert went out for PULSE_GAP_HOURS,
    a short data update (no AI, no advice): busiest topics among the 50 + top newswire items."""
    from . import db, influence
    from .pro import _kv

    if not config.TELEGRAM_PRO_CHAT_ID:
        return False
    now = db.now()
    last = _kv("pro_last_msg") or ""
    if not force and (now.hour not in PULSE_HOURS_UTC or last > db.ago(hours=PULSE_GAP_HOURS)):
        return False
    topics = [c for c in influence.consensus(hours=6, top=10) if c["accounts"] >= 2][:3]
    news = db.rows("""SELECT name, teaser, url, story, impact FROM news WHERE labeled = 1 AND impact >= 6
                      AND teaser IS NOT NULL AND teaser != '' AND published_at >= :s
                      ORDER BY impact DESC, published_at DESC LIMIT 8""", s=db.ago(hours=4))
    seen, picked = set(), []
    for n in news:
        if (n["story"] or n["url"]) in seen:
            continue
        seen.add(n["story"] or n["url"])
        picked.append(n)
        if len(picked) == 2:
            break
    if not topics and not picked:
        return False
    tracked = db.rows("SELECT COUNT(*) AS n FROM accounts WHERE active = 1")[0]["n"]
    lines = [f"📡 <b>IOI Pulse</b> · {now.strftime('%H:%M')} UTC · quiet market, no surge right now"]
    if topics:
        lines.append("\n<b>Most discussed (last 6h)</b>")
        for c in topics:
            trend = "above" if c["surge"] >= 1.2 else ("below" if c["surge"] < 0.8 else "around")
            lines.append(f"• <b>{html.escape(c['topic'])}</b>: {c['accounts']} of {tracked} accounts, {trend} normal. "
                         f"First: {html.escape(c['first']['name'])}")
    if picked:
        lines.append("\n<b>Newswire</b>")
        for n in picked:
            lines.append(f"• {html.escape(n['teaser'])} <a href=\"{n['url']}\">{html.escape(n['name'])}</a>")
    lines.append("\n<i>You get an instant alert as soon as a topic surges. Attention data, not financial advice.</i>")
    return send(config.TELEGRAM_PRO_CHAT_ID, "\n".join(lines))
