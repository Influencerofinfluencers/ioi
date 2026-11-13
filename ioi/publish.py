"""Static output for the free website + RSS (GitHub Pages or Cloudflare Pages)."""
import html
import json
from datetime import datetime, timezone

import markdown as md_lib
from feedgen.feed import FeedGenerator

from . import config, db, influence

CSS = """body{font-family:system-ui,-apple-system,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem;
line-height:1.6;color:#1a1a1a}a{color:#0b5cad}table{border-collapse:collapse;width:100%}
td,th{padding:.4rem .5rem;border-bottom:1px solid #e5e5e5;text-align:left}th{font-size:.85rem;color:#666}
header{display:flex;justify-content:space-between;align-items:baseline;border-bottom:2px solid #1a1a1a;margin-bottom:1rem}
.small{font-size:.85rem;color:#666}"""


def _page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="alternate" type="application/rss+xml" title="{html.escape(config.BRAND)}" href="{config.SITE_URL}/feed.xml">
<style>{CSS}</style></head><body>
<header><strong><a href="{config.SITE_URL}/">{html.escape(config.BRAND)}</a></strong>
<span class="small"><a href="{config.SITE_URL}/leaderboard.html">Top 50</a> · <a href="{config.SITE_URL}/feed.xml">RSS</a></span></header>
{body}
<p class="small">{html.escape(config.DISCLAIMER)}</p></body></html>"""


def _dt(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def build_site() -> dict:
    out = config.PUBLIC_DIR
    (out / "daily").mkdir(parents=True, exist_ok=True)
    digests = db.rows("SELECT * FROM digests ORDER BY day DESC LIMIT 60")
    alerts = db.rows("SELECT * FROM alerts ORDER BY created_at DESC LIMIT 50")
    reports = db.rows("SELECT * FROM reports WHERE kind = 'weekly' ORDER BY created_at DESC LIMIT 20")
    (out / "weekly").mkdir(parents=True, exist_ok=True)
    for r in reports:
        (out / "weekly" / f"{r['id']}.html").write_text(
            _page(r["title"], md_lib.markdown(r["markdown"], extensions=["tables"])), encoding="utf-8"
        )

    # daily pages + index
    items = []
    for d in digests:
        body = md_lib.markdown(d["markdown"], extensions=["tables"])
        (out / "daily" / f"{d['day']}.html").write_text(_page(d["title"], body), encoding="utf-8")
        items.append(f'<li><a href="{config.SITE_URL}/daily/{d["day"]}.html">{html.escape(d["title"])}</a> <span class="small">{d["day"]}</span></li>')
    latest = md_lib.markdown(digests[0]["markdown"], extensions=["tables"]) if digests else "<p>First edition coming soon.</p>"
    (out / "index.html").write_text(
        _page(config.BRAND, latest + "<h2>Archive</h2><ul>" + "".join(items) + "</ul>"), encoding="utf-8"
    )

    # leaderboard (the viral asset: accounts share their own rank)
    board = influence.leaderboard(days=7)
    (out / "leaderboard.json").write_text(json.dumps(board, indent=1), encoding="utf-8")
    trs = "".join(
        f"<tr><td>{r['rank']}</td><td>{html.escape(r['name'])}</td><td>{r['platform']}</td>"
        f"<td>{r['first_moves']}</td><td>{r['echoes']}</td><td>{r['score']}</td></tr>"
        for r in board
    )
    table = (
        "<h1>IOI Top 50 - who really moves the conversation</h1>"
        "<p>Ranked on the last 7 days: 3 points for every topic an account raised first "
        "that others then picked up, 1 point for every account that echoed them within 24h. "
        "Followers do not count.</p>"
        "<table><tr><th>#</th><th>Account</th><th>Platform</th><th>First moves</th>"
        f"<th>Echoes</th><th>Score</th></tr>{trs}</table>"
        f'<p class="small">Updated {db.iso(db.now())}</p>'
    )
    (out / "leaderboard.html").write_text(_page("IOI Top 50", table), encoding="utf-8")

    # RSS: daily digests + alerts in one feed
    fg = FeedGenerator()
    fg.title(config.BRAND)
    fg.link(href=config.SITE_URL, rel="alternate")
    fg.link(href=f"{config.SITE_URL}/feed.xml", rel="self")
    fg.description("What the most influential crypto voices say - who said it first, and where they agree.")
    fg.language("en")
    for d in reversed(digests[:30]):
        e = fg.add_entry()
        e.id(f"{config.SITE_URL}/daily/{d['day']}.html")
        e.title(d["title"])
        e.link(href=f"{config.SITE_URL}/daily/{d['day']}.html")
        e.description(md_lib.markdown(d["markdown"]))
        e.pubDate(_dt(d["created_at"]))
    for r in reversed(reports[:10]):
        e = fg.add_entry()
        e.id(f"{config.SITE_URL}/weekly/{r['id']}.html")
        e.title(r["title"])
        e.link(href=f"{config.SITE_URL}/weekly/{r['id']}.html")
        e.description(md_lib.markdown(r["markdown"]))
        e.pubDate(_dt(r["created_at"]))
    for a in reversed(alerts[:30]):
        e = fg.add_entry()
        e.id(f"{config.SITE_URL}/alerts/{a['id']}")
        e.title(f"Attention surge: {a['topic']}")
        e.link(href=f"{config.SITE_URL}/leaderboard.html")
        e.description(html.escape(a["message"]))
        e.pubDate(_dt(a["created_at"]))
    fg.rss_file(str(out / "feed.xml"), pretty=True)
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return {"digests": len(digests), "alerts": len(alerts), "weekly": len(reports), "leaderboard": len(board), "dir": str(out)}
