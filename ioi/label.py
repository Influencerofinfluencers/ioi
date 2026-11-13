"""Bulk labelling: topics, category, sentiment, relevance and a one-line summary.

Runs on the cheap model (LABEL_MODEL). Posts are sent in batches of 25, so a
day of ~350 posts is about 14 requests.
"""
import json
import logging

from . import config, db, llm

log = logging.getLogger("ioi.label")

BATCH = 25
def system_prompt() -> str:
    return f"""You label social-media posts for an attention-analytics product for {config.AUDIENCE}.
For EACH post return an object with:
- "i": the post index you were given
- "topics": up to 5 normalised topics. {config.TOPIC_RULES}
- "category": one of {config.CATEGORIES}
- "sentiment": number from -1 (very negative) to 1 (very positive), 0 = neutral
- "relevance": 0-10, how newsworthy for {config.AUDIENCE} (memes, giveaways, ads = 0-2)
- "format": one of ["news", "analysis", "chart_data", "thread", "question", "meme", "video", "promo", "other"]
- "hook": the opening technique, one of ["breaking", "number", "question", "bold_claim", "story", "list", "none"]
- "summary": max 20 words, neutral, in English, your own words (no quotes)
Return ONLY a JSON array, nothing else."""


def _prompt(batch: list[dict]) -> str:
    lines = []
    for i, p in enumerate(batch):
        text = " ".join((p["text"] or "").split())[:1200]
        lines.append(f'{i}. [{p["platform"]} | {p["name"]}] {text}')
    return "Posts:\n" + "\n".join(lines)


def label_pending(limit: int = 500) -> dict:
    posts = db.rows(
        """SELECT p.id, p.platform, p.text, a.name FROM posts p
           JOIN accounts a ON a.key = p.account_key
           WHERE p.labeled = 0 ORDER BY p.created_at LIMIT :n""",
        n=limit,
    )
    stats = {"labeled": 0, "skipped_short": 0, "errors": 0}
    # very short posts carry no signal: mark done without spending tokens
    todo = []
    for p in posts:
        if len((p["text"] or "").strip()) < 15:
            db.run("UPDATE posts SET labeled = 1 WHERE id = :id", id=p["id"])
            stats["skipped_short"] += 1
        else:
            todo.append(p)
    for start in range(0, len(todo), BATCH):
        batch = todo[start : start + BATCH]
        try:
            out = llm.parse_json(
                llm.complete(system_prompt(), _prompt(batch), config.LABEL_MODEL, max_tokens=4000, provider=config.LABEL_PROVIDER)
            )
        except Exception as e:  # noqa: BLE001
            log.warning("labelling batch failed: %s", e)
            stats["errors"] += 1
            continue
        for item in out if isinstance(out, list) else []:
            try:
                p = batch[int(item["i"])]
            except (KeyError, ValueError, IndexError, TypeError):
                continue
            topics = sorted({str(t).upper().lstrip("$").strip() for t in item.get("topics", []) if t})
            db.run(
                """INSERT INTO labels (post_id, topics, category, sentiment, relevance, summary, format, hook)
                   VALUES (:id, :t, :c, :s, :r, :sum, :fmt, :hook)
                   ON CONFLICT (post_id) DO UPDATE SET topics = excluded.topics,
                     category = excluded.category, sentiment = excluded.sentiment,
                     relevance = excluded.relevance, summary = excluded.summary,
                     format = excluded.format, hook = excluded.hook""",
                id=p["id"],
                t=json.dumps(topics[:5]),
                c=item.get("category", "other"),
                s=max(-1.0, min(1.0, float(item.get("sentiment", 0) or 0))),
                r=max(0, min(10, int(item.get("relevance", 0) or 0))),
                sum=str(item.get("summary", ""))[:300],
                fmt=str(item.get("format", "other"))[:20],
                hook=str(item.get("hook", "none"))[:20],
            )
            db.run("UPDATE posts SET labeled = 1 WHERE id = :id", id=p["id"])
            stats["labeled"] += 1
    return stats
