"""IOI Pro access: Whop memberships <-> private Telegram channel, run every cycle (10 min).

Flow
 1. Buyer pays on Whop, then sends their Whop e-mail address to our bot in a private chat.
 2. We check Whop: active/trialing membership of the Pro product with that e-mail?
    yes -> personal single-use invite link (valid 24h). One Telegram account per membership.
 3. Every run we compare linked members with Whop. Membership no longer valid ->
    removed from the channel (ban + unban, so they can come back after renewing) and told how to renew.
Nothing here can charge, refund or change a membership: the Whop key is read-only.
"""
import html
import json
import logging
import re
import time

import httpx

from . import config, db, notify

log = logging.getLogger("ioi.pro")
VALID = {"active", "trialing", "completed"}  # "completed" = paid one-time plans
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


# ---------- Whop ----------

def whop_members() -> dict[str, dict]:
    """{membership_id: {"email", "status", "user_id"}} for the Pro product (all statuses)."""
    out, after = {}, None
    headers = {"Authorization": f"Bearer {config.WHOP_API_KEY}", "Api-Version-Date": config.WHOP_API_VERSION}
    for _ in range(50):  # max 50 pages of 100
        params = {"company_id": config.WHOP_COMPANY_ID, "first": 100}
        if after:
            params["after"] = after
        r = httpx.get(f"{config.WHOP_API_BASE}/memberships", params=params, headers=headers, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"Whop {r.status_code}: {r.text[:300]}")
        body = r.json()
        for m in body.get("data", []):
            prod = m.get("product") or {}
            prod_id = prod.get("id") if isinstance(prod, dict) else prod
            if config.WHOP_PRODUCT_ID and prod_id and prod_id != config.WHOP_PRODUCT_ID:
                continue
            user = m.get("user") or {}
            email = (user.get("email") if isinstance(user, dict) else None) or m.get("email") or ""
            out[m["id"]] = {"email": email.strip().lower(), "status": m.get("status", ""),
                            "user_id": user.get("id") if isinstance(user, dict) else user}
        info = body.get("page_info") or {}
        after = info.get("end_cursor")
        if not info.get("has_next_page") or not after:
            break
    return out


# ---------- Telegram bot API ----------

def tg(method: str, **params) -> dict:
    r = httpx.post(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{method}", json=params, timeout=40)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"telegram {method}: {data.get('description')}")
    return data["result"]


def dm(user_id: int, text: str):
    try:
        tg("sendMessage", chat_id=user_id, text=text, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:  # noqa: BLE001 - user may have blocked the bot
        log.warning("dm to %s failed: %s", user_id, e)


def _kv(key: str, value: str | None = None) -> str | None:
    if value is None:
        r = db.rows("SELECT value FROM kv WHERE key = :k", k=key)
        return r[0]["value"] if r else None
    db.run("DELETE FROM kv WHERE key = :k", k=key)
    db.run("INSERT INTO kv (key, value) VALUES (:k, :v)", k=key, v=value)
    return value


# ---------- main ----------

def enabled() -> bool:
    return bool(config.WHOP_API_KEY and config.WHOP_COMPANY_ID and config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_PRO_CHAT_ID)


HELP = ("👋 This is the IOI bot.\n\n<b>IOI Pro member?</b> Send me the e-mail address you used on Whop "
        "and I'll send your personal invite to the live-alerts channel.\n\n"
        "Not a member yet? {checkout}")


def handle_messages(members: dict | None) -> tuple[int, dict | None]:
    offset = int(_kv("tg_offset") or 0)
    updates = tg("getUpdates", offset=offset, timeout=0, allowed_updates=["message"])
    invited = 0
    checkout = config.PRO_CHECKOUT_URL or config.SITE_URL + "/#pricing"
    for u in updates:
        _kv("tg_offset", str(u["update_id"] + 1))
        msg = u.get("message") or {}
        chat, sender = msg.get("chat") or {}, msg.get("from") or {}
        if chat.get("type") != "private" or sender.get("is_bot"):
            continue
        uid, text = sender["id"], (msg.get("text") or "").strip()
        m = EMAIL.search(text)
        if not m:
            dm(uid, HELP.format(checkout=checkout))
            continue
        email = m.group().lower()
        if members is None:
            members = whop_members()
        mine = [(mid, v) for mid, v in members.items() if v["email"] == email and v["status"] in VALID]
        if not mine:
            dm(uid, f"I couldn't find an active IOI Pro membership for <b>{html.escape(email)}</b>.\n"
                    f"Check the spelling (it must be your Whop e-mail), or get Pro here: {checkout}")
            continue
        mid = mine[0][0]
        row = db.rows("SELECT * FROM pro_members WHERE membership_id = :m", m=mid)
        if row and row[0]["tg_user_id"] and int(row[0]["tg_user_id"]) != uid:
            dm(uid, "This membership is already linked to another Telegram account. "
                    "If that's wrong, reply here and the owner will check it.")
            notify.status(f"IOI Pro: membership {mid} claimed by a second Telegram account ({uid}) - check.")
            continue
        link = tg("createChatInviteLink", chat_id=config.TELEGRAM_PRO_CHAT_ID, name=mid[:32],
                  member_limit=1, expire_date=int(time.time()) + 24 * 3600)["invite_link"]
        db.run("DELETE FROM pro_members WHERE membership_id = :m", m=mid)
        db.run("""INSERT INTO pro_members (membership_id, email, tg_user_id, status, invited_at)
                  VALUES (:m, :e, :u, 'invited', :t)""", m=mid, e=email, u=str(uid), t=db.iso(db.now()))
        dm(uid, f"✅ Welcome to IOI Pro! Here is your personal invite (works once, valid 24h):\n{link}")
        invited += 1
    return invited, members


def remove_expired(members: dict) -> int:
    removed = 0
    for r in db.rows("SELECT * FROM pro_members WHERE status <> 'removed'"):
        cur = members.get(r["membership_id"])
        if cur and cur["status"] in VALID:
            continue
        uid = int(r["tg_user_id"])
        try:
            tg("banChatMember", chat_id=config.TELEGRAM_PRO_CHAT_ID, user_id=uid)
            tg("unbanChatMember", chat_id=config.TELEGRAM_PRO_CHAT_ID, user_id=uid, only_if_banned=True)
        except Exception as e:  # noqa: BLE001
            notify.status(f"IOI Pro: could not remove {uid} from the Pro channel: {e}")
            continue
        db.run("UPDATE pro_members SET status = 'removed' WHERE membership_id = :m", m=r["membership_id"])
        dm(uid, "Your IOI Pro membership has ended, so you've been removed from the live-alerts channel. "
                f"Renew any time: {config.PRO_CHECKOUT_URL or config.SITE_URL}")
        removed += 1
    return removed


def sync() -> dict:
    if not enabled():
        return {"skipped": "WHOP_API_KEY / WHOP_COMPANY_ID / TELEGRAM_PRO_CHAT_ID missing"}
    invited, members = handle_messages(None)
    removed = 0
    # membership check: every run when someone is linked (one cheap API call)
    if db.rows("SELECT COUNT(*) AS n FROM pro_members WHERE status <> 'removed'")[0]["n"]:
        members = members if members is not None else whop_members()
        removed = remove_expired(members)
    if invited or removed:
        notify.status(f"IOI Pro: {invited} new member(s) invited, {removed} removed.")
    return {"invited": invited, "removed": removed}


def check() -> dict:
    """Setup check without personal data: bot rights in the Pro channel + Whop access."""
    out = {}
    me = tg("getMe")
    out["bot"] = "@" + me["username"]
    cm = tg("getChatMember", chat_id=config.TELEGRAM_PRO_CHAT_ID, user_id=me["id"])
    out["bot_status_in_pro_channel"] = cm.get("status")
    out["can_invite_users"] = cm.get("can_invite_users")
    out["can_restrict_members"] = cm.get("can_restrict_members")
    try:
        ms = whop_members()
        out["whop_ok"] = True
        out["whop_memberships"] = len(ms)
        out["whop_statuses"] = sorted({m["status"] for m in ms})
        out["whop_emails_visible"] = all(m["email"] for m in ms) if ms else "no members yet"
    except Exception as e:  # noqa: BLE001
        out["whop_ok"] = False
        out["whop_error"] = str(e)[:300]
    notify.status("IOI Pro setup check:\n" + json.dumps(out, indent=1))
    return out
