"""Reply scout: finds the biggest fresh posts of tracked X accounts on topics that are hot right now,
writes a short data reply for each, and sends them to the owner's Telegram status chat.

The owner posts them by hand (one tap): X forbids automated unsolicited replies.
"""
import html
import json
import logging
import re

from . import config, db, influence, llm, notify, pro

log = logging.getLogger("ioi.scout")
EXPIRE_HOURS = 3

SYSTEM = """You write short reply suggestions for the X account of {brand}, which tracks what the 50 most
influential {audience} voices say and who said it first.
For each post you get the post summary plus IOI data about the post's broad TOPIC (e.g. all BTC talk).
Write ONE reply per post that adds a real data point from IOI.
CRUCIAL - be accurate: topic_accounts_24h, topic_first_voice and topic_surge describe the WHOLE topic over
24 hours, NOT this specific story. Never claim that those accounts discussed this specific post/event, or
that topic_first_voice was first on this story. Correct: "BTC is the loudest topic in our tracker today:
21 of 50 accounts, ~3x normal." Wrong: "21 of 50 accounts picked up this purchase."
You may also skip the numbers and react to the post with a sharp observation linked to the topic data. Max 200 characters. Sound like a sharp human analyst, not a bot: never start with "IOI" or a label, vary the
opening (a reaction to the post, a question, a number), refer to what the post is about, and use "we track"
at most once. Friendly, specific, no hype, no hashtags, no links, no @mentions, no exact timestamps unless
it is striking (e.g. "hours before everyone else").
Never give buy/sell advice or price predictions. {rules}
Return a JSON list: [{{"i": <index>, "reply": "<text>"}}]"""


def candidates(n: int = 5, hours: int = 4) -> list[dict]:
    hot = {c["topic"]: c for c in influence.consensus(hours=24, top=15) if c["accounts"] >= 3}
    if not hot:
        return []
    seen_accounts, out = set(), []
    tracked = db.rows("SELECT COUNT(*) AS n FROM accounts WHERE active = 1")[0]["n"]
    for m in sorted(influence.mentions(db.ago(hours=hours)), key=lambda m: -((m["likes"] or 0) + 2 * (m["reposts"] or 0))):
        if m["topic"] not in hot or m["account_key"] in seen_accounts or "x.com" not in (m["url"] or ""):
            continue
        if sum(1 for o in out if o["topic"] == m["topic"]) >= 2:  # variety: max 2 suggestions per topic
            continue
        c = hot[m["topic"]]
        seen_accounts.add(m["account_key"])
        out.append({"url": m["url"], "name": m["name"], "summary": m["summary"], "likes": m["likes"],
                    "topic": m["topic"], "accounts": c["accounts"], "tracked": tracked,
                    "first": c["first"]["name"], "first_at": c["first"]["at"], "surge": c["surge"]})
        if len(out) >= n:
            break
    return out


def run_once() -> dict:
    posts = candidates()
    if not posts:
        return {"skipped": "no hot posts right now"}
    data = [{"name": p["name"], "post_summary": p["summary"], "topic": p["topic"],
             "topic_accounts_24h": f'{p["accounts"]} of {p["tracked"]}', "topic_first_voice": p["first"],
             "topic_first_at": p["first_at"], "topic_surge": p["surge"]} for p in posts]
    out = llm.complete(
        SYSTEM.format(brand=config.BRAND, audience=config.AUDIENCE, rules=config.CONTENT_RULES),
        "Posts (JSON, index = position):\n" + json.dumps(data, ensure_ascii=False, default=str),
        config.LABEL_MODEL, max_tokens=1500, provider=config.LABEL_PROVIDER,
    )
    from .social import ADVICE, clean

    replies = {}
    for item in llm.parse_json(out) or []:
        try:
            text = clean(item["reply"])[:240]
            if text and not ADVICE.search(text):
                replies[int(item["i"])] = text
        except (KeyError, TypeError, ValueError):
            continue
    sent = 0
    for i, p in enumerate(posts):
        tid = tweet_id(p["url"])
        if i not in replies or not tid:
            continue
        pro._kv(f"reply-{tid}", json.dumps({"text": replies[i], "url": p["url"], "state": "new", "at": db.iso(db.now())}))
        msg = (f'\U0001F4AC <b>Reply {sent + 1}</b> · {html.escape(p["name"])} · {html.escape(p["topic"])} · {p["likes"]} likes\n'
               f'{p["url"]}\n\n<code>{html.escape(replies[i])}</code>\n\n'
               f'<i>Tik op de knop: X opent met je reply al ingevuld, jij tikt op Beantwoorden.</i>')
        if notify.send(config.TELEGRAM_STATUS_CHAT_ID, msg, buttons=[[
                {"text": "\u21A9\uFE0F Antwoord op X", "url": intent_url(tid, replies[i])}]]):
            sent += 1
    return {"sent": sent} if sent else {"skipped": "no usable replies"}


def intent_url(tid: str, text: str) -> str:
    """X web intent: opens the reply composer under that post with the text filled in (user posts)."""
    from urllib.parse import quote

    return f"https://x.com/intent/post?in_reply_to={tid}&text={quote(text)}"


def tweet_id(url: str) -> str | None:
    m = re.search(r"/status/(\d+)", url or "")
    return m.group(1) if m else None


def _ack(cq: dict, text: str):
    try:
        pro.tg("answerCallbackQuery", callback_query_id=cq["id"], text=text[:180])
    except Exception:  # noqa: BLE001 - a late answer must never stop the handling
        pass


def handle_button(cq: dict) -> str:
    """Buttons from older suggestion messages: the X API refuses unsolicited replies (403),
    so these now only explain the new 'Antwoord op X' button."""
    chat = str(((cq.get("message") or {}).get("chat") or {}).get("id", ""))
    if chat != str(config.TELEGRAM_STATUS_CHAT_ID):
        _ack(cq, "Not allowed")
        return "ignored"
    _ack(cq, "Oude knop - nieuwe voorstellen hebben een 'Antwoord op X'-knop")
    try:
        pro.tg("editMessageReplyMarkup", chat_id=chat, message_id=cq["message"]["message_id"],
               reply_markup={"inline_keyboard": []})
    except Exception:  # noqa: BLE001
        pass
    return "legacy"


def _old_handle_button(cq: dict) -> str:  # kept for reference, not used
    """Owner tapped a button under a suggestion. Only the owner's status chat counts."""
    chat = str(((cq.get("message") or {}).get("chat") or {}).get("id", ""))
    if chat != str(config.TELEGRAM_STATUS_CHAT_ID):
        pro.tg("answerCallbackQuery", callback_query_id=cq["id"], text="Not allowed")
        return "ignored"
    action, _, tid = (cq.get("data") or "").partition(":")
    raw = pro._kv(f"reply-{tid}")
    item = json.loads(raw) if raw else None
    msg_id = cq["message"]["message_id"]
    if not item or item.get("state") != "new":
        pro.tg("answerCallbackQuery", callback_query_id=cq["id"], text="Al afgehandeld")
        return "done-before"
    if item.get("at", "9") < db.ago(hours=EXPIRE_HOURS):  # a reply on an old post looks odd: let it expire
        item["state"] = "expired"
        pro._kv(f"reply-{tid}", json.dumps(item))
        pro.tg("answerCallbackQuery", callback_query_id=cq["id"], text="Verlopen (ouder dan 3 uur)")
        pro.tg("editMessageReplyMarkup", chat_id=chat, message_id=msg_id, reply_markup={"inline_keyboard": []})
        return "expired"
    if action == "rs":
        item["state"] = "skipped"
        result = "\u274C Overgeslagen"
    else:
        from .social import post_x_reply

        try:
            url = post_x_reply(item["text"], tid)
            item["state"], item["posted"] = "posted", url
            result = f"\u2705 Geplaatst: {url}"
        except Exception as e:  # noqa: BLE001
            item["state"] = "failed"
            result = ("\u26A0\uFE0F X weigerde de reply (" + html.escape(str(e)[:120]) + "). "
                      "Plaats hem zelf: open de link en plak de tekst.")
    pro._kv(f"reply-{tid}", json.dumps(item))
    pro.tg("answerCallbackQuery", callback_query_id=cq["id"], text=result[:180])
    pro.tg("editMessageReplyMarkup", chat_id=chat, message_id=msg_id, reply_markup={"inline_keyboard": []})
    notify.send(chat, result)
    return item["state"]
