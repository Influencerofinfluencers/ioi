"""Automatic highlight posts on X, Bluesky and the free Telegram channel.

One post = one real finding from the data ("8 of our 50 accounts started on X, @A was first").
Every platform is optional: missing keys = that platform is skipped silently.
Safety: hard length limits, no advice wording, max SOCIAL_MAX_PER_DAY, never the same topic twice
in 24h, kill switch SOCIAL_ENABLED=0.
"""
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from urllib.parse import quote

import httpx

from . import cards, config, db, influence, llm, notify

log = logging.getLogger("ioi.social")

SYSTEM = """You write one short social media post for {brand}, an account that tracks what the
50 most influential {audience} voices say and who said it first.
Voice: {voice}
Rules:
- Use ONLY the data given. Never invent numbers, names or events.
- One finding: the topic, how many of the tracked accounts talk about it, who was first and when.
- Max 240 characters. No hashtags except at most one cashtag like $BTC. No emojis except one at the start.
- No links, no @mentions. Plain text.
- Never tell people to buy, sell or hold anything. No price predictions. {rules}
- Language: {lang}.
Return only the post text."""

ADVICE = re.compile(r"\b(buy|sell|hold|long|short)\s+(now|it|this|the dip)\b|\bnot? financial advice\b|\bto the moon\b", re.I)
URL = re.compile(r"https?://\S+|www\.\S+")


def pick() -> dict | None:
    """Best fresh finding of the last 12h that we have not posted about in 24h."""
    posted = {r["topic"] for r in db.rows("SELECT topic FROM social_posts WHERE created_at >= :s", s=db.ago(hours=24))}
    for c in influence.consensus(hours=12, top=10):
        if c["topic"] not in posted and c["accounts"] >= 3:
            return c
    return None


def clean(text: str) -> str:
    text = URL.sub("", text).strip().strip('"').strip()
    text = re.sub(r"(?<!\w)@(\w+)", r"\1", text)  # no tagging
    text = re.sub(r"\s+\n", "\n", text)
    if len(text) > 260:
        text = text[:257].rsplit(" ", 1)[0] + "..."
    return text


def write(c: dict) -> str:
    data = {k: c[k] for k in ("topic", "accounts", "names", "usual_per_day", "surge", "sentiment", "first")}
    data["tracked_accounts"] = db.rows("SELECT COUNT(*) AS n FROM accounts WHERE active = 1")[0]["n"]
    text = llm.complete(
        SYSTEM.format(
            brand=config.BRAND, audience=config.AUDIENCE, voice=config.VOICE,
            rules=config.CONTENT_RULES, lang=config.DIGEST_LANGUAGE,
        ),
        "Data (JSON):\n" + json.dumps(data, ensure_ascii=False, default=str),
        config.WRITE_MODEL,
        max_tokens=400,
        provider=config.WRITE_PROVIDER,
    )
    text = clean(text)
    if not text or ADVICE.search(text):
        raise ValueError(f"post rejected by safety check: {text[:120]!r}")
    return text


# ---------- platforms ----------

def _oauth1(method: str, url: str) -> str:
    p = {
        "oauth_consumer_key": config.X_API_KEY,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": config.X_ACCESS_TOKEN,
        "oauth_version": "1.0",
    }
    enc = lambda s: quote(str(s), safe="~")  # noqa: E731
    base = "&".join([method.upper(), enc(url), enc("&".join(f"{enc(k)}={enc(v)}" for k, v in sorted(p.items())))])
    key = f"{enc(config.X_API_SECRET)}&{enc(config.X_ACCESS_SECRET)}"
    p["oauth_signature"] = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{enc(k)}="{enc(v)}"' for k, v in sorted(p.items()))


X_MEDIA = {"last": None}  # result of the last X media upload, reported in the status message


def _x_upload(image) -> str | None:
    """Upload a PNG to X (v2 media endpoint, v1.1 as backup). None = post without image."""
    X_MEDIA["last"] = None
    problems = []
    for url, id_of in ((f"{config.X_API_BASE}/media/upload", lambda j: (j.get("data") or {}).get("id")),
                       ("https://upload.twitter.com/1.1/media/upload.json", lambda j: j.get("media_id_string"))):
        try:
            with open(image, "rb") as fh:
                r = httpx.post(url, headers={"Authorization": _oauth1("POST", url)},
                               files={"media": ("card.png", fh, "image/png")},
                               data={"media_category": "tweet_image"}, timeout=60)
            if r.status_code in (200, 201) and id_of(r.json()):
                X_MEDIA["last"] = "ok"
                return id_of(r.json())
            log.warning("X media upload %s: %s %s", url, r.status_code, r.text[:200])
            problems.append(f"{url.split('/')[2]} {r.status_code} {r.text[:120]}")
        except Exception as e:  # noqa: BLE001
            log.warning("X media upload failed: %s", e)
            problems.append(str(e)[:120])
    X_MEDIA["last"] = "failed: " + " | ".join(problems)
    return None


def post_x(text: str, image=None) -> str:
    if not all([config.X_API_KEY, config.X_API_SECRET, config.X_ACCESS_TOKEN, config.X_ACCESS_SECRET]):
        return "skipped"
    url = f"{config.X_API_BASE}/tweets"
    body = {"text": text}
    media_id = _x_upload(image) if image else None
    if media_id:
        body["media"] = {"media_ids": [media_id]}
    r = httpx.post(url, json=body, headers={"Authorization": _oauth1("POST", url)}, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"X {r.status_code}: {r.text[:200]}")
    return "https://x.com/i/status/" + r.json()["data"]["id"]


def post_x_reply(text: str, in_reply_to: str) -> str:
    """Only ever called after the owner tapped 'Plaats op X' for this exact text."""
    if not all([config.X_API_KEY, config.X_API_SECRET, config.X_ACCESS_TOKEN, config.X_ACCESS_SECRET]):
        raise RuntimeError("X keys missing")
    text = clean(text)
    if ADVICE.search(text):
        raise ValueError("safety check")
    url = f"{config.X_API_BASE}/tweets"
    r = httpx.post(url, json={"text": text, "reply": {"in_reply_to_tweet_id": in_reply_to}},
                   headers={"Authorization": _oauth1("POST", url)}, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"X {r.status_code}: {r.text[:200]}")
    return "https://x.com/i/status/" + r.json()["data"]["id"]


def _facets(text: str) -> list:
    out = []
    for m in URL.finditer(text):
        start = len(text[: m.start()].encode())
        end = start + len(m.group().encode())
        out.append({"index": {"byteStart": start, "byteEnd": end},
                    "features": [{"$type": "app.bsky.richtext.facet#link", "uri": m.group()}]})
    return out


def post_bluesky(text: str, image=None) -> str:
    if not (config.BLUESKY_HANDLE and config.BLUESKY_APP_PASSWORD):
        return "skipped"
    base = "https://bsky.social/xrpc"
    s = httpx.post(f"{base}/com.atproto.server.createSession",
                   json={"identifier": config.BLUESKY_HANDLE, "password": config.BLUESKY_APP_PASSWORD}, timeout=30)
    if s.status_code != 200:
        raise RuntimeError(f"Bluesky login {s.status_code}: {s.text[:200]}")
    sess = s.json()
    record = {"$type": "app.bsky.feed.post", "text": text, "createdAt": db.iso(db.now()), "langs": ["en"]}
    if f := _facets(text):
        record["facets"] = f
    if image:
        try:
            up = httpx.post(f"{base}/com.atproto.repo.uploadBlob", content=open(image, "rb").read(), timeout=60,
                            headers={"Authorization": f"Bearer {sess['accessJwt']}", "Content-Type": "image/png"})
            if up.status_code == 200:
                record["embed"] = {"$type": "app.bsky.embed.images",
                                   "images": [{"alt": text[:290], "image": up.json()["blob"]}]}
        except Exception as e:  # noqa: BLE001
            log.warning("bluesky image upload failed: %s", e)
    r = httpx.post(f"{base}/com.atproto.repo.createRecord",
                   headers={"Authorization": f"Bearer {sess['accessJwt']}"},
                   json={"repo": sess["did"], "collection": "app.bsky.feed.post", "record": record}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Bluesky {r.status_code}: {r.text[:200]}")
    return "https://bsky.app/profile/" + config.BLUESKY_HANDLE + "/post/" + r.json()["uri"].rsplit("/", 1)[-1]


def post_telegram(text: str, image=None) -> str:
    if not config.TELEGRAM_PUBLIC_CHAT_ID:
        return "skipped"
    if image and notify.photo(config.TELEGRAM_PUBLIC_CHAT_ID, image, notify.html.escape(text, quote=False)):
        return "ok"
    return "ok" if notify.send(config.TELEGRAM_PUBLIC_CHAT_ID, notify.html.escape(text, quote=False)) else "failed"


# ---------- main ----------

def run_once() -> dict:
    if not config.SOCIAL_ENABLED:
        return {"skipped": "SOCIAL_ENABLED=0"}
    today = db.rows("SELECT COUNT(*) AS n FROM social_posts WHERE created_at >= :s", s=db.ago(hours=24))[0]["n"]
    if today >= config.SOCIAL_MAX_PER_DAY:
        return {"skipped": f"already {today} posts in 24h"}
    c = pick()
    if not c:
        return {"skipped": "nothing new worth posting"}
    text = write(c)
    tracked = db.rows("SELECT COUNT(*) AS n FROM accounts WHERE active = 1")[0]["n"]
    image = cards.highlight(c, tracked)
    return _publish(c["topic"], text, image)


def _publish(topic: str, text: str, image=None, extra_status: str = "") -> dict:
    link = config.SITE_URL + "/"
    with_link = f"{text}\n\n{link}"
    results, errors = {}, []
    for name, fn, body in (("x", post_x, text), ("bluesky", post_bluesky, with_link), ("telegram", post_telegram, with_link)):
        try:
            results[name] = fn(body, image)
            if name == "x" and image and X_MEDIA["last"]:
                results["x_image"] = X_MEDIA["last"]
                if X_MEDIA["last"] != "ok":
                    errors.append("X posted without card: " + X_MEDIA["last"])
        except Exception as e:  # noqa: BLE001 - one platform failing must not stop the others
            results[name] = "error"
            errors.append(f"{name}: {e}")
    db.run("INSERT INTO social_posts (id, created_at, topic, text, results) VALUES (:id, :c, :t, :x, :r)",
           id=f"{topic}-{db.iso(db.now())}", c=db.iso(db.now()), t=topic, x=text, r=json.dumps(results))
    posted = [k for k, v in results.items() if k in ("x", "bluesky", "telegram") and v not in ("skipped", "error", "failed")]
    notify.status(f"IOI posted ({', '.join(posted) or 'nowhere'}{', with card' if image else ''}):\n{text}"
                  + ("\nERRORS: " + "; ".join(errors) if errors else "") + extra_status)
    return {"topic": topic, "text": text, "results": results, "errors": errors, "image": bool(image)}


def weekly_top50() -> dict:
    """Monday: the ranking card. Tags are NOT automated (X rules); the owner gets a ready tag-reply."""
    board = influence.leaderboard(days=7)
    if not board or board[0]["score"] <= 0:
        return {"skipped": "no ranking yet"}
    week = "WEEK " + db.now().strftime("%V")
    top = board[:3]
    text = ("\U0001F3C6 IOI Top 50, " + week.title() + ": who really moved crypto, ranked on who raised topics first, "
            "not on followers. 1. " + top[0]["name"] + " 2. " + top[1]["name"] + " 3. " + top[2]["name"])
    handles = " ".join("@" + r["handle"].lstrip("@") for r in top if r["platform"] == "x")
    tip = (f"\n\nTAG-TIP (zelf plaatsen, 10 sec): reply op je eigen X-post met:\n"
           f"Congrats {handles} \U0001F44F Full ranking in our bio." if handles else "")
    return _publish("TOP50-" + week, clean(text), cards.top50(board, week), extra_status=tip)
