"""Reply scout: finds the biggest fresh posts of tracked X accounts on topics that are hot right now,
writes a short data reply for each, and sends them to the owner's Telegram status chat.

The owner posts them by hand (one tap): X forbids automated unsolicited replies.
"""
import html
import json
import logging

from . import config, db, influence, llm, notify

log = logging.getLogger("ioi.scout")

SYSTEM = """You write short reply suggestions for the X account of {brand}, which tracks what the 50 most
influential {audience} voices say and who said it first.
For each post you get the post summary plus IOI data about that topic. Write ONE reply per post that adds
a real data point from IOI (how many of the tracked accounts talk about it, who was first and when,
how far above normal). Max 200 characters. Sound like a sharp human analyst, not a bot: never start with "IOI" or a label, vary the
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
    data = [{k: p[k] for k in ("name", "summary", "topic", "accounts", "tracked", "first", "first_at", "surge")}
            for p in posts]
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
    lines = ["\U0001F4AC <b>Reply-voorstellen</b> (open de link, plak de tekst, klaar):"]
    for i, p in enumerate(posts):
        if i in replies:
            lines.append(f'\n<b>{html.escape(p["name"])}</b> · {html.escape(p["topic"])} · {p["likes"]} likes\n'
                         f'{p["url"]}\n<code>{html.escape(replies[i])}</code>')
    if len(lines) == 1:
        return {"skipped": "no usable replies"}
    notify.send(config.TELEGRAM_STATUS_CHAT_ID, "\n".join(lines))
    return {"sent": len(lines) - 1}
