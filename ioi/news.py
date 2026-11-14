"""Newswire layer: the fastest official / primary sources, kept SEPARATE from the 50 tracked voices
(so "N of 50 accounts" stays clean). Used for:
  - Pro "newswire" alerts: a market-moving item (listing, regulator, ETF filing, hack) the moment it
    appears, with how many of the 50 voices already talk about it;
  - "who was faster than the news" stats for the digest and the ranking;
  - creative teasers in our own words (never copied article text; always a link to the source).

Rules (agreed with the owner): only official APIs / RSS, polite polling, no bypassing bot protection,
no paywalled or licensed content, headline facts + own teaser + link, no buy/sell language.
"""
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import httpx

from . import config, db, influence, llm, notify

log = logging.getLogger("ioi.news")
UA = {"User-Agent": "IOI-Influencer-of-Influencers/1.0 (+https://influencerofinfluencers.github.io/ioi/)"}
CRYPTO = re.compile(r"\b(crypto|bitcoin|btc|ether(eum)?|eth|solana|sol|xrp|stablecoin|digital asset|token|blockchain|defi|etf)\b", re.I)

# kind: listing | regulator | filing | hack | media | wire
SOURCES = [
    # exchange listings (official endpoints)
    {"key": "binance_listings", "name": "Binance listings", "kind": "listing", "type": "binance"},
    # Upbit blocks cloud IPs (403) - not bypassed; its listings arrive via BWEnews / Tree News
    {"key": "coinbase_assets", "name": "Coinbase Assets", "kind": "listing", "type": "x", "handle": "CoinbaseAssets"},
    {"key": "kraken_blog", "name": "Kraken", "kind": "listing", "type": "rss", "url": "https://blog.kraken.com/feed"},
    # regulators + filings (public sector)
    {"key": "sec_press", "name": "SEC", "kind": "regulator", "type": "rss", "url": "https://www.sec.gov/news/pressreleases.rss"},
    {"key": "cftc_press", "name": "CFTC", "kind": "regulator", "type": "rss", "url": "https://www.cftc.gov/RSS/RSSGP/rssgp.xml", "filter": True},
    {"key": "fed_press", "name": "Federal Reserve", "kind": "regulator", "type": "rss", "url": "https://www.federalreserve.gov/feeds/press_all.xml"},
    {"key": "sec_s1", "name": "SEC EDGAR S-1", "kind": "filing", "type": "edgar", "filter": True,
     "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=S-1&company=&dateb=&owner=include&count=40&output=atom"},
    {"key": "sec_19b4", "name": "SEC EDGAR 19b-4", "kind": "filing", "type": "edgar", "filter": True,
     "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=19b-4&company=&dateb=&owner=include&count=40&output=atom"},
    # hack / exploit alerts
    {"key": "peckshield", "name": "PeckShieldAlert", "kind": "hack", "type": "x", "handle": "PeckShieldAlert"},
    {"key": "certik", "name": "CertiK Alert", "kind": "hack", "type": "x", "handle": "CertiKAlert"},
    {"key": "binance_delist", "name": "Binance delistings", "kind": "listing", "type": "binance", "catalog": 161},
    {"key": "okx_listings", "name": "OKX listings", "kind": "listing", "type": "okx"},
    {"key": "hyperliquid_perps", "name": "Hyperliquid new markets", "kind": "listing", "type": "hyperliquid"},
    {"key": "hyperliquid_ann", "name": "Hyperliquid", "kind": "listing", "type": "telegram", "handle": "hyperliquid_announcements"},
    {"key": "coinbase_status", "name": "Coinbase status", "kind": "outage", "type": "rss",
     "url": "https://status.coinbase.com/history.rss"},
    # law enforcement (seizures, arrests, indictments)
    {"key": "doj_press", "name": "US Justice Dept", "kind": "regulator", "type": "rss", "filter": True,
     "url": "https://www.justice.gov/news/rss?type=press_release&m=1"},
    # fast wires
    {"key": "bwenews", "name": "BWEnews", "kind": "wire", "type": "telegram", "handle": "BWEnews"},
    {"key": "walterbloomberg", "name": "Walter Bloomberg", "kind": "wire", "type": "telegram", "handle": "walterbloomberg"},
    {"key": "zachxbt_tg", "name": "ZachXBT Investigations", "kind": "hack", "type": "telegram", "handle": "investigations"},
    # Trump's posts move markets; unofficial public archive of Truth Social, always labelled as such
    {"key": "trump_truth", "name": "Trump on Truth Social (via trumpstruth.org)", "kind": "wire", "type": "rss",
     "url": "https://trumpstruth.org/feed"},
    # prediction markets: big odds moves on crypto questions
    {"key": "polymarket", "name": "Polymarket", "kind": "market", "type": "polymarket"},
    # protocol / tech releases
    {"key": "btc_core", "name": "Bitcoin Core releases", "kind": "tech", "type": "rss",
     "url": "https://github.com/bitcoin/bitcoin/releases.atom"},
    {"key": "eth_blog", "name": "Ethereum Foundation blog", "kind": "tech", "type": "rss", "url": "https://blog.ethereum.org/feed.xml"},
    # established media (headline + link only)
    {"key": "coindesk_rss", "name": "CoinDesk", "kind": "media", "type": "rss", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"key": "cointelegraph_rss", "name": "Cointelegraph", "kind": "media", "type": "rss", "url": "https://cointelegraph.com/rss"},
    {"key": "theblock_rss", "name": "The Block", "kind": "media", "type": "rss", "url": "https://www.theblock.co/rss.xml"},
    {"key": "btcmag_rss", "name": "Bitcoin Magazine", "kind": "media", "type": "rss", "url": "https://bitcoinmagazine.com/.rss/full/"},
    {"key": "decrypt_rss", "name": "Decrypt", "kind": "media", "type": "rss", "url": "https://decrypt.co/feed"},
    {"key": "blockworks_rss", "name": "Blockworks", "kind": "media", "type": "rss", "url": "https://blockworks.co/feed"},
    {"key": "dlnews_rss", "name": "DL News", "kind": "media", "type": "rss", "url": "https://www.dlnews.com/arc/outboundfeeds/rss/"},
    # aggregator (only if a free CryptoPanic token is configured)
    {"key": "cryptopanic", "name": "CryptoPanic", "kind": "media", "type": "cryptopanic"},
]
FAST_KINDS = {"listing", "regulator", "filing", "hack", "wire", "outage", "market"}
MAX_FAILS = 5


def _state(key: str) -> dict:
    from .pro import _kv

    raw = _kv(f"news-{key}")
    return json.loads(raw) if raw else {}


def _save_state(key: str, st: dict):
    from .pro import _kv

    _kv(f"news-{key}", json.dumps(st))


def _ts(value) -> str:
    if isinstance(value, (int, float)):
        return db.iso(datetime.fromtimestamp(value / (1000 if value > 1e11 else 1), tz=timezone.utc))
    try:
        return db.iso(parsedate_to_datetime(value))
    except Exception:  # noqa: BLE001
        try:
            return db.iso(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
        except Exception:  # noqa: BLE001
            return db.iso(db.now())


# ---------- fetchers: each returns [{"title", "url", "at"}] ----------

def _rss(src):
    body = src.get("_body")
    if body is None:
        r = httpx.get(src["url"], headers=UA, timeout=30, follow_redirects=True)
        r.raise_for_status()
        body = r.content
    feed = feedparser.parse(body)
    out = []
    for e in feed.entries[:30]:
        title = (e.get("title") or "").strip()
        if not title:
            continue
        if src.get("filter") and not CRYPTO.search(title + " " + (e.get("summary") or "")):
            continue
        when = e.get("published") or e.get("updated") or ""
        out.append({"title": title[:300], "url": e.get("link") or src["url"], "at": _ts(when)})
    return out


def _edgar(src):
    """SEC fair-access policy: requests must carry a contact e-mail in the User-Agent.
    Only active when the owner set SEC_CONTACT_EMAIL."""
    if not config.SEC_CONTACT_EMAIL:
        return None
    r = httpx.get(src["url"], headers={"User-Agent": f"IOI Influencer of Influencers {config.SEC_CONTACT_EMAIL}"},
                  timeout=30, follow_redirects=True)
    r.raise_for_status()
    return _rss({**src, "_body": r.content})


def _binance(src):
    r = httpx.get("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query",
                  params={"type": 1, "catalogId": src.get("catalog", 48), "pageNo": 1, "pageSize": 15},
                  headers=UA, timeout=30)
    r.raise_for_status()
    cats = (r.json().get("data") or {}).get("catalogs") or []
    arts = cats[0].get("articles", []) if cats else []
    if not cats:
        raise ValueError("unexpected Binance response: " + r.text[:120])
    return [{"title": a["title"], "url": f"https://www.binance.com/en/support/announcement/{a['code']}",
             "at": _ts(a.get("releaseDate") or 0)} for a in arts if a.get("title")]


def _upbit(src):
    r = httpx.get("https://api-manager.upbit.com/api/v1/announcements",
                  params={"os": "web", "page": 1, "per_page": 20, "category": "trade"}, headers=UA, timeout=30)
    r.raise_for_status()
    notices = (r.json().get("data") or {}).get("notices") or []
    return [{"title": n["title"], "url": f"https://upbit.com/service_center/notice?id={n['id']}",
             "at": _ts(n.get("listed_at") or n.get("first_listed_at") or "")} for n in notices if n.get("title")]


def _okx(src):
    r = httpx.get("https://www.okx.com/api/v5/support/announcements",
                  params={"annType": "announcements-new-listings"}, headers=UA, timeout=30)
    r.raise_for_status()
    data = r.json().get("data") or []
    details = data[0].get("details", []) if data else []
    return [{"title": d["title"], "url": d.get("url") or "https://www.okx.com/help/section/announcements-new-listings",
             "at": _ts(int(d.get("pTime") or 0))} for d in details if d.get("title")]


def _hyperliquid(src):
    """New perpetual markets: compare the live market list with the one we saw last time."""
    r = httpx.post("https://api.hyperliquid.xyz/info", json={"type": "meta"}, timeout=30)
    r.raise_for_status()
    names = sorted({u["name"] for u in r.json().get("universe", []) if not u.get("isDelisted")})
    if not names:
        raise ValueError("Hyperliquid meta: empty universe")
    st = _state(src["key"])
    known = set(st.get("markets") or [])
    st["markets"] = names
    _save_state(src["key"], {**_state(src["key"]), "markets": names})
    if not known:  # first run: remember, report nothing
        return []
    now = db.iso(db.now())
    return [{"title": f"Hyperliquid lists a new perpetual market: {n}-USD", "at": now,
             "url": f"https://app.hyperliquid.xyz/trade/{n}"} for n in names if n not in known]


POLY_MOVE = 0.10  # report a crypto prediction market when its odds move 10+ points between checks


def _polymarket(src):
    r = httpx.get("https://gamma-api.polymarket.com/markets",
                  params={"limit": 200, "active": "true", "closed": "false", "order": "volume24hr", "ascending": "false"},
                  headers=UA, timeout=30)
    r.raise_for_status()
    st = _state(src["key"])
    last = st.get("odds") or {}
    odds, out = {}, []
    for m in r.json():
        q = m.get("question") or ""
        if not CRYPTO.search(q) or float(m.get("volume24hr") or 0) < 50_000:
            continue
        try:
            p = float(json.loads(m.get("outcomePrices") or "[]")[0])
        except (ValueError, IndexError, TypeError):
            continue
        key = str(m.get("id"))
        odds[key] = p
        if key in last and abs(p - last[key]) >= POLY_MOVE:
            out.append({"title": f"Polymarket: odds on \"{q}\" moved from {last[key]:.0%} to {p:.0%}",
                        "url": f"https://polymarket.com/event/{m.get('slug') or ''}", "at": db.iso(db.now()),
                        "uid": f"{p:.2f}"})
    _save_state(src["key"], {**_state(src["key"]), "odds": odds})
    return out


def _cryptopanic(src):
    if not config.CRYPTOPANIC_TOKEN:
        return None  # not configured: skipped silently
    r = httpx.get("https://cryptopanic.com/api/developer/v2/posts/",
                  params={"auth_token": config.CRYPTOPANIC_TOKEN, "filter": "important", "public": "true"},
                  headers=UA, timeout=30)
    r.raise_for_status()
    return [{"title": p["title"], "url": p.get("url") or "https://cryptopanic.com",
             "at": _ts(p.get("published_at") or "")} for p in r.json().get("results", [])[:30] if p.get("title")]


def _social(src):
    from .sources import SourceNotConfigured
    from .sources import telegram as tg_src
    from .sources import x as x_src

    if src["type"] == "x" and db.x_reads_today() >= config.X_MAX_READS_PER_DAY:
        return None  # shared X budget reached
    st = _state(src["key"])
    acc = {"key": src["key"], "handle": src["handle"], "last_seen_id": st.get("last_seen_id"),
           "platform_user_id": st.get("platform_user_id")}
    mod = x_src if src["type"] == "x" else tg_src
    try:
        posts = mod.fetch(acc)
    except SourceNotConfigured:
        return None
    if posts:
        st["last_seen_id"] = mod.newest_id(posts)
    st["platform_user_id"] = acc.get("platform_user_id")
    _save_state(src["key"], {**_state(src["key"]), **st})
    return [{"title": re.sub(r"\s+", " ", p["text"])[:300], "url": p["url"], "at": p["created_at"],
             "x_read": src["type"] == "x"} for p in posts if p.get("text")]


FETCH = {"rss": _rss, "edgar": _edgar, "binance": _binance, "okx": _okx, "hyperliquid": _hyperliquid,
         "polymarket": _polymarket, "upbit": _upbit, "cryptopanic": _cryptopanic, "x": _social, "telegram": _social}


def fetch_all() -> dict:
    stats = {"new": 0, "errors": [], "x_reads": 0}
    for src in SOURCES:
        st = _state(src["key"])
        if st.get("paused_until", "") > db.iso(db.now()):
            continue
        try:
            items = FETCH[src["type"]](src)
        except Exception as e:  # noqa: BLE001 - a source failing never stops the others
            st = _state(src["key"])
            st["fails"] = st.get("fails", 0) + 1
            if st["fails"] >= MAX_FAILS:
                st["paused_until"], st["fails"] = db.ago(hours=-24), 0
                notify.status(f"IOI newswire: {src['name']} paused 24h after {MAX_FAILS} errors ({str(e)[:150]})")
            _save_state(src["key"], st)
            stats["errors"].append(f"{src['key']}: {str(e)[:120]}")
            continue
        if items is None:
            continue
        st = _state(src["key"])
        st["fails"] = 0
        _save_state(src["key"], st)
        for it in items:
            stats["x_reads"] += 1 if it.get("x_read") else 0
            if it["at"] < db.ago(hours=48):  # no backlog of old items on first run
                continue
            nid = src["key"] + ":" + hashlib.sha1((it["url"] + it.get("uid", "")).encode()).hexdigest()[:16]
            if db.rows("SELECT 1 AS x FROM news WHERE id = :id", id=nid):
                continue
            db.run("""INSERT INTO news (id, source, name, kind, title, url, published_at, fetched_at, labeled)
                      VALUES (:id, :s, :n, :k, :t, :u, :p, :f, 0)""",
                   id=nid, s=src["key"], n=src["name"], k=src["kind"], t=it["title"], u=it["url"],
                   p=min(it["at"], db.iso(db.now())), f=db.iso(db.now()))
            stats["new"] += 1
    return stats


# ---------- labelling + teasers ----------

SYSTEM = """You label crypto news headlines for {brand} and write a teaser for each.
For each item return:
- topics: 1-3, {topic_rules}
- impact: 1-10 (9-10 = market-moving right now: a major exchange listing, an ETF filing/approval,
  a big hack, a regulator action; 1-3 = minor). General wires (Walter Bloomberg, Trump on Truth Social)
  also carry non-crypto news: anything without a clear link to crypto, rates, the dollar or risk markets
  gets impact 1. A Trump post counts only when it is about crypto, tariffs, the Fed or the economy, and the
  teaser must say it is a Truth Social post (never present it as confirmed policy).
- story: a short lowercase slug naming the underlying event (e.g. "doj-seizes-dprk-binance-funds").
  Several outlets covering the same event MUST get the same slug. If an event matches one of the
  known stories listed below, reuse that exact slug.
- teaser: max 140 characters, in your OWN words (never reuse the headline's wording or structure).
  Make it scroll-stopping: open with a sharp hook (a surprising number, a contrast, a short question,
  or a "why it matters" angle), then land the key fact. Vary the style between items. Concrete beats
  vague. Factual only: no invented details, no hype words like "moon", at most one emoji at the start,
  never buy/sell advice or price predictions. {rules}
Good teaser examples (style only):
- "$1.2B back on the table: US prosecutors move to claw back North Korea's Binance haul."
- "Three weeks of gains, gone by lunch. Bitcoin slips to a 12-week low as the dollar flexes."
- "Who blinked first? The SEC just put a spot SOL ETF decision on the calendar."
Return a JSON list: [{{"i": <index>, "topics": [...], "impact": <int>, "story": "<slug>", "teaser": "<text>"}}]"""


def label_pending(limit: int = 30, batch: int = 10) -> dict:
    total = 0
    for _ in range(max(1, limit // batch)):
        n = _label_batch(batch)
        total += n
        if n == 0:
            break
    return {"labeled": total}


def _label_batch(limit: int) -> int:
    items = db.rows("SELECT * FROM news WHERE labeled = 0 ORDER BY published_at DESC LIMIT :n", n=limit)
    if not items:
        return 0
    lines = "\n".join(f"{i}. [{it['name']}] {it['title']}" for i, it in enumerate(items))
    known = [r["story"] for r in db.rows("""SELECT story, MAX(published_at) AS p FROM news WHERE story IS NOT NULL
                                             AND published_at >= :s GROUP BY story ORDER BY p DESC LIMIT 40""",
                                          s=db.ago(hours=36))]
    out = llm.complete(SYSTEM.format(brand=config.BRAND, topic_rules=config.TOPIC_RULES, rules=config.CONTENT_RULES)
                       + ("\nKnown stories: " + ", ".join(known) if known else ""),
                       "Headlines:\n" + lines, config.LABEL_MODEL, max_tokens=4000, provider=config.LABEL_PROVIDER)
    from .social import ADVICE

    parsed = llm.parse_json(out) or []
    if isinstance(parsed, dict):  # {"items": [...]} or similar wrapper
        parsed = next((v for v in parsed.values() if isinstance(v, list)), [parsed])
    done = 0
    for res in parsed:
        if not isinstance(res, dict):
            continue
        try:
            it = items[int(res["i"])]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        teaser = re.sub(r"https?://\S+", "", str(res.get("teaser") or "")).strip()[:200]
        story = re.sub(r"[^a-z0-9-]", "", str(res.get("story") or "").lower())[:80] or None
        if ADVICE.search(teaser):
            teaser = ""
        db.run("UPDATE news SET topics = :t, impact = :i, teaser = :te, story = :st, labeled = 1 WHERE id = :id",
               st=story,
               t=json.dumps([str(x).upper() for x in (res.get("topics") or [])][:3]),
               i=int(res.get("impact") or 0), te=teaser, id=it["id"])
        done += 1
    # anything the model skipped: mark labeled so it never blocks the queue
    db.run("UPDATE news SET labeled = 1 WHERE labeled = 0 AND fetched_at < :t", t=db.ago(hours=2))
    return done


# ---------- analysis ----------

def voices_on(topic: str, hours: int = 6) -> tuple[int, str | None]:
    ms = [m for m in influence.mentions(db.ago(hours=hours)) if m["topic"] == topic]
    first = min((m["created_at"] for m in ms), default=None)
    return len({m["account_key"] for m in ms}), first


def newswire_alerts(min_impact: int = 8, max_age_min: int = 45) -> list[dict]:
    """Pro: a market-moving primary-source item, the moment it lands."""
    sent = []
    for it in db.rows("""SELECT * FROM news WHERE labeled = 1 AND alerted IS NULL AND impact >= :i
                         AND published_at >= :since ORDER BY published_at""",
                      i=min_impact, since=db.ago(minutes=max_age_min)):
        dup = it.get("story") and db.rows("""SELECT 1 FROM news WHERE story = :s AND id != :id AND alerted IS NOT NULL
                                             AND alerted != 'skip' AND published_at >= :since LIMIT 1""",
                                          s=it["story"], id=it["id"], since=db.ago(hours=24))
        if it["kind"] not in FAST_KINDS or dup:
            db.run("UPDATE news SET alerted = 'skip' WHERE id = :id", id=it["id"])
            continue
        topics = json.loads(it["topics"] or "[]")
        n, _ = voices_on(topics[0]) if topics else (0, None)
        tracked = db.rows("SELECT COUNT(*) AS n FROM accounts WHERE active = 1")[0]["n"]
        t = topics[0] if topics else "this topic"
        lead = (f"quiet so far: none of our {tracked} voices posted about {t} in the last 6h" if n == 0
                else f"{n} of {tracked} voices active on {t} in the last 6h")
        text = (f"\U0001F5DE <b>NEWSWIRE · {it['name']}</b>\n{notify.html.escape(it['teaser'] or it['title'])}\n"
                f"<i>{lead}</i>\n<a href=\"{it['url']}\">Source</a>")
        notify.send(config.TELEGRAM_PRO_CHAT_ID, text)
        db.run("UPDATE news SET alerted = :t WHERE id = :id", t=db.iso(db.now()), id=it["id"])
        sent.append(it["title"][:80])
    return sent


def for_digest(hours: int = 24, top: int = 5) -> list[dict]:
    """Biggest primary-source items of the day + whether the voices were earlier than the news."""
    out, seen = [], set()
    for it in db.rows("""SELECT * FROM news WHERE labeled = 1 AND impact >= 6 AND published_at >= :s
                         ORDER BY impact DESC, published_at LIMIT :n""", s=db.ago(hours=hours), n=top * 6):
        key = it.get("story") or it["id"]
        if key in seen:
            continue
        seen.add(key)
        topics = json.loads(it["topics"] or "[]")
        n, first_voice = voices_on(topics[0], hours=hours + 6) if topics else (0, None)
        lead = None
        if first_voice:
            delta = (datetime.fromisoformat(it["published_at"].replace("Z", "+00:00"))
                     - datetime.fromisoformat(first_voice.replace("Z", "+00:00"))).total_seconds() / 60
            lead = round(delta)  # >0: voices were earlier than the news by N minutes
        out.append({"source": it["name"], "kind": it["kind"], "teaser": it["teaser"] or it["title"],
                    "url": it["url"], "topic": topics[0] if topics else None, "voices": n,
                    "voices_minutes_ahead": lead})
        if len(out) >= top:
            break
    return out


def cycle() -> dict:
    f = fetch_all()
    lab = label_pending()
    alerts = newswire_alerts()
    return {"new": f["new"], "errors": len(f["errors"]), "x_reads": f["x_reads"], "labeled": lab["labeled"],
            "alerts": alerts}
