"""Weekly playbook: reverse-engineer WHY the top posts of the week worked.

Each post is compared with its own account's normal engagement (so a small
account's breakout counts as much as a giant's), then the patterns behind the
outliers (format, hook, timing, topic) are counted and Claude writes a short,
practical playbook. Sellable as a report to creators, brands and shops.
"""
import json
import statistics
from collections import Counter, defaultdict

from . import config, db, llm

SYSTEM = """You write the "{brand} Weekly Playbook" for {audience}.
You get the posts that over-performed this week (engagement versus that account's own
average) plus counts of their formats, hooks, posting hours and topics.
Write in {lang}, Markdown, max 450 words:
# <title>
One-sentence summary of the week.
## What worked
4-6 bullets. Each bullet = one pattern, backed by the numbers given (e.g. "5 of the top 10
were charts"). Explain WHY it likely worked in one short sentence.
## Copy this next week
3 concrete content ideas the reader can make themselves, based on the patterns.
## Best time to post
One or two sentences based only on the hour data (UTC).
Rules: use only the data given, never invent numbers, never quote posts verbatim,
link examples as [Name](url). {rules}"""


def _score(p: dict) -> float:
    return (p["likes"] or 0) + 2 * (p["reposts"] or 0) + 1.5 * (p["replies"] or 0) + (p["views"] or 0) / 200


def outliers(days: int = 7, top: int = 15) -> dict:
    posts = db.rows(
        """SELECT p.id, p.account_key, p.url, p.created_at, p.likes, p.replies, p.reposts, p.views,
                  p.platform, a.name, l.topics, l.format, l.hook, l.summary, l.category
           FROM posts p JOIN labels l ON l.post_id = p.id JOIN accounts a ON a.key = p.account_key
           WHERE p.created_at >= :s AND l.category <> 'promo_spam'""",
        s=db.ago(days=days),
    )
    by_acc = defaultdict(list)
    for p in posts:
        by_acc[p["account_key"]].append(_score(p))
    ranked = []
    for p in posts:
        base = statistics.median(by_acc[p["account_key"]]) if by_acc[p["account_key"]] else 0
        if len(by_acc[p["account_key"]]) < 3:  # too little history to judge
            continue
        ranked.append({**p, "lift": round(_score(p) / max(base, 1.0), 2)})
    ranked.sort(key=lambda p: p["lift"], reverse=True)
    best = ranked[:top]
    return {
        "posts_analysed": len(posts),
        "top": [
            {k: p[k] for k in ("name", "platform", "url", "summary", "format", "hook", "lift", "created_at")}
            for p in best
        ],
        "formats": Counter(p["format"] or "other" for p in best).most_common(),
        "hooks": Counter(p["hook"] or "none" for p in best).most_common(),
        "hours_utc": Counter(int(p["created_at"][11:13]) for p in best).most_common(6),
        "topics": Counter(t for p in best for t in json.loads(p["topics"] or "[]")).most_common(8),
    }


def build() -> dict:
    data = outliers()
    if len(data["top"]) < 5:
        raise RuntimeError("not enough labelled posts this week for a playbook (need 5+)")
    md = llm.complete(
        SYSTEM.format(brand=config.BRAND, audience=config.AUDIENCE, lang=config.DIGEST_LANGUAGE,
                      rules=config.CONTENT_RULES),
        "This week's data (JSON):\n" + json.dumps(data, ensure_ascii=False, default=str),
        config.WRITE_MODEL,
        max_tokens=1800,
        provider=config.WRITE_PROVIDER,
    ).strip()
    title = next((ln.lstrip("# ").strip() for ln in md.splitlines() if ln.startswith("# ")), "Weekly Playbook")
    week = db.iso(db.now())[:10]
    db.run(
        """INSERT INTO reports (id, kind, title, markdown, created_at) VALUES (:id, 'weekly', :t, :m, :c)
           ON CONFLICT (id) DO UPDATE SET title = excluded.title, markdown = excluded.markdown""",
        id=f"weekly-{week}",
        t=title,
        m=md,
        c=db.iso(db.now()),
    )
    return {"id": f"weekly-{week}", "title": title, "markdown": md, "data": data}
