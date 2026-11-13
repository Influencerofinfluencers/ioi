"""Daily digest written by the strong model (WRITE_MODEL) from the analysed data."""
import json

from . import config, db, influence, llm

SYSTEM = """You write "{brand} Daily", a short attention briefing for {audience}.
Voice: {voice}
Rules:
- Use ONLY the data given. Never invent numbers, quotes or events.
- Write in your own words; never copy posts verbatim.
- Every item ends with source links in Markdown: [Name](url).
- {rules}
- Language: {lang}.
Keep it under 500 words in total. Structure (Markdown):
# <catchy title, max 8 words>
One-sentence intro.
## What the top accounts are talking about
4-5 items, biggest consensus first, max 60 words each. For each: **TOPIC**, how many tracked accounts,
who raised it first, the mood, 1-2 sentences of context.
## Who moved first
1-2 sentences on the account that led the most conversations.
## Quick hits
3 bullets with other notable single posts.
End with the disclaimer line in italics: {disclaimer}"""


def build(day: str | None = None) -> dict:
    day = day or db.iso(db.now())[:10]
    data = {
        "date": day,
        "consensus": influence.consensus(hours=24, top=8),
        "leaders": influence.leaderboard(days=1)[:5],
        "quick_hits": db.rows(
            """SELECT a.name, l.summary, p.url FROM posts p
               JOIN labels l ON l.post_id = p.id JOIN accounts a ON a.key = p.account_key
               WHERE p.created_at >= :s AND l.relevance >= 6 AND l.category <> 'promo_spam'
               ORDER BY l.relevance DESC, p.likes DESC LIMIT 10""",
            s=db.ago(hours=24),
        ),
    }
    if not data["consensus"] and not data["quick_hits"]:
        raise RuntimeError("no labelled posts in the last 24h - run fetch + label first")
    md = llm.complete(
        SYSTEM.format(
            lang=config.DIGEST_LANGUAGE, disclaimer=config.DISCLAIMER, brand=config.BRAND,
            audience=config.AUDIENCE, voice=config.VOICE, rules=config.CONTENT_RULES,
        ),
        "Data for today (JSON):\n" + json.dumps(data, ensure_ascii=False, default=str)[:60000],
        config.WRITE_MODEL,
        max_tokens=5000,
        provider=config.WRITE_PROVIDER,
    ).strip()
    title = next((ln.lstrip("# ").strip() for ln in md.splitlines() if ln.startswith("# ")), f"IOI Daily {day}")
    db.run(
        """INSERT INTO digests (day, title, markdown, created_at) VALUES (:d, :t, :m, :c)
           ON CONFLICT (day) DO UPDATE SET title = excluded.title, markdown = excluded.markdown,
             created_at = excluded.created_at""",
        d=day,
        t=title,
        m=md,
        c=db.iso(db.now()),
    )
    return {"day": day, "title": title, "markdown": md}
