"""The core of IOI: who said it first, who followed, and where the top accounts agree."""
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta

from . import config, db

MIN_RELEVANCE = 3


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def mentions(since: str) -> list[dict]:
    """One row per (post, topic) for relevant, non-spam posts since a timestamp."""
    out = []
    for r in db.rows(
        """SELECT p.id, p.account_key, p.url, p.created_at, p.likes, p.replies, p.reposts,
                  p.views, a.name, a.followers, l.topics, l.sentiment, l.relevance, l.summary,
                  l.category
           FROM posts p JOIN labels l ON l.post_id = p.id JOIN accounts a ON a.key = p.account_key
           WHERE p.created_at >= :since AND l.relevance >= :rel AND l.category <> 'promo_spam'
           ORDER BY p.created_at""",
        since=since,
        rel=MIN_RELEVANCE,
    ):
        for t in json.loads(r["topics"] or "[]"):
            out.append({**r, "topic": t})
    return out


def consensus(hours: int = 24, top: int = 15) -> list[dict]:
    """Topics ranked by how many distinct tracked accounts talk about them now,
    compared with their usual level over the previous 7 days."""
    now = db.now()
    window_start = db.iso(now - timedelta(hours=hours))
    base_start = db.iso(now - timedelta(hours=hours) - timedelta(days=7))
    all_m = mentions(base_start)
    active = db.rows("SELECT COUNT(*) AS n FROM accounts WHERE active = 1")[0]["n"] or 1

    current, baseline = defaultdict(list), defaultdict(set)
    for m in all_m:
        if m["created_at"] >= window_start:
            current[m["topic"]].append(m)
        else:
            day = m["created_at"][:10]
            baseline[m["topic"]].add((day, m["account_key"]))

    results = []
    for topic, ms in current.items():
        accounts = {m["account_key"]: m["name"] for m in ms}
        usual = len(baseline[topic]) / 7  # avg distinct accounts per day before
        first = min(ms, key=lambda m: m["created_at"])
        results.append(
            {
                "topic": topic,
                "accounts": len(accounts),
                "share": round(len(accounts) / active, 3),
                "names": sorted(accounts.values()),
                "usual_per_day": round(usual, 2),
                "surge": round(len(accounts) / max(usual, 0.5), 1),
                "sentiment": round(sum(m["sentiment"] or 0 for m in ms) / len(ms), 2),
                "first": {"name": first["name"], "at": first["created_at"], "url": first["url"]},
                "posts": [
                    {"name": m["name"], "summary": m["summary"], "url": m["url"], "at": m["created_at"]}
                    for m in sorted(ms, key=lambda m: -(m["relevance"] or 0))[:6]
                ],
            }
        )
    results.sort(key=lambda r: (r["accounts"], r["surge"]), reverse=True)
    return results[:top]


def leaderboard(days: int = 7) -> list[dict]:
    """Rank accounts by real influence, not followers:
    3 points per topic they raised first (and others followed),
    1 point per account that echoed them within ECHO_WINDOW_HOURS,
    plus a small engagement bonus."""
    since = db.ago(days=days, hours=config.ECHO_WINDOW_HOURS)
    period_start = db.ago(days=days)
    echo = timedelta(hours=config.ECHO_WINDOW_HOURS)
    by_topic = defaultdict(list)
    for m in mentions(since):
        by_topic[m["topic"]].append(m)

    # sets, so a post tagged with two topics (BTC + ETF) is not counted twice
    score = defaultdict(lambda: {"first_posts": set(), "echo_pairs": set(), "eng": 0, "topics": set()})
    for topic, ms in by_topic.items():
        ms.sort(key=lambda m: m["created_at"])
        first_seen = {}
        for m in ms:
            first_seen.setdefault(m["account_key"], m)
        for key, m in first_seen.items():
            if m["created_at"] < period_start:
                continue
            t0 = _parse(m["created_at"])
            # accounts that mentioned the topic in the echo window after this one,
            # and had not mentioned it themselves in the 24h before
            followers = {
                o["account_key"]
                for o in ms
                if o["account_key"] != key
                and t0 < _parse(o["created_at"]) <= t0 + echo
                and not any(
                    x["account_key"] == o["account_key"]
                    and t0 - timedelta(hours=24) <= _parse(x["created_at"]) <= t0
                    for x in ms
                )
            }
            s = score[key]
            s["echo_pairs"].update((m["id"], f) for f in followers)
            s["topics"].add(topic)
            if followers and min(first_seen.values(), key=lambda v: v["created_at"]) is m:
                s["first_posts"].add(m["id"])
    # engagement per account over the period
    for r in db.rows(
        """SELECT account_key, AVG(likes + 2 * reposts + replies) AS eng
           FROM posts WHERE created_at >= :since GROUP BY account_key""",
        since=period_start,
    ):
        score[r["account_key"]]["eng"] = r["eng"] or 0

    accounts = {a["key"]: a for a in db.rows("SELECT * FROM accounts WHERE active = 1")}
    board = []
    for key, a in accounts.items():
        s = score.get(key, {"first_posts": set(), "echo_pairs": set(), "eng": 0, "topics": set()})
        first_moves = len(s["first_posts"])
        echoes = len(s["echo_pairs"])
        total = 3 * first_moves + echoes + round(math.log10(1 + (s["eng"] or 0)), 2)
        board.append(
            {
                "key": key,
                "name": a["name"],
                "platform": a["platform"],
                "handle": a["handle"],
                "followers": a["followers"],
                "first_moves": first_moves,
                "echoes": echoes,
                "topics": len(s["topics"]),
                "score": round(total, 2),
            }
        )
    board.sort(key=lambda r: (r["score"], r["followers"] or 0), reverse=True)
    for i, r in enumerate(board, 1):
        r["rank"] = i
    return board


def new_alerts() -> list[dict]:
    """A topic that many tracked accounts start talking about at once."""
    created = []
    recent = {r["topic"] for r in db.rows("SELECT topic FROM alerts WHERE created_at >= :s", s=db.ago(hours=24))}
    for c in consensus(hours=config.ALERT_WINDOW_HOURS, top=50):
        if c["accounts"] < config.ALERT_MIN_ACCOUNTS or c["topic"] in recent or c["surge"] < 2:
            continue
        msg = (
            f"{c['topic']}: {c['accounts']} tracked accounts in the last "
            f"{config.ALERT_WINDOW_HOURS}h (usually {c['usual_per_day']}/day). "
            f"First: {c['first']['name']}. Mood: {c['sentiment']:+.2f}."
        )
        alert = {
            "id": f"{c['topic']}-{db.iso(db.now())}",
            "topic": c["topic"],
            "created_at": db.iso(db.now()),
            "accounts": json.dumps(c["names"]),
            "message": msg,
            "url": c["first"]["url"],
        }
        db.run(
            "INSERT INTO alerts (id, topic, created_at, accounts, message) VALUES (:id, :topic, :created_at, :accounts, :message)",
            **{k: alert[k] for k in ("id", "topic", "created_at", "accounts", "message")},
        )
        created.append(alert)
    return created
