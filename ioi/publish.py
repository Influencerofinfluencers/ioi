"""Static output for the free website + RSS (GitHub Pages or Cloudflare Pages)."""
import html
import shutil
import json
from datetime import datetime, timezone

import markdown as md_lib
from feedgen.feed import FeedGenerator

from . import config, db, influence

CSS = """body{font-family:Inter,system-ui,-apple-system,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem;
line-height:1.6;color:#0E1116;background:#F6F6F2}a{color:#0B0D12}h1,h2,h3{font-family:'Space Grotesk',system-ui,sans-serif}
table{border-collapse:collapse;width:100%}td,th{padding:.4rem .5rem;border-bottom:1px solid #E2E2DC;text-align:left}
th{font-size:.85rem;color:#5B616B}header{display:flex;justify-content:space-between;align-items:baseline;
border-bottom:2px solid #0E1116;margin-bottom:1rem;padding-bottom:.5rem}header strong a{text-decoration:none}
.small{font-size:.85rem;color:#5B616B}.cta{background:#C8FF3D;padding:.9rem 1.1rem;border-radius:12px;margin:1.5rem 0}"""


def _page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="icon" href="{config.SITE_URL}/assets/IOI-favicon.ico">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&family=Space+Grotesk:wght@500;700&display=swap" rel="stylesheet">
<link rel="alternate" type="application/rss+xml" title="{html.escape(config.BRAND)}" href="{config.SITE_URL}/feed.xml">
<style>{CSS}</style></head><body>
<header><strong><a href="{config.SITE_URL}/">{html.escape(config.BRAND)}</a></strong>
<span class="small"><a href="{config.SITE_URL}/daily/">Archive</a> · <a href="{config.SITE_URL}/leaderboard.html">Top 50</a> · <a href="{config.SITE_URL}/feed.xml">RSS</a></span></header>
{body}{_cta()}
<p class="small">{html.escape(config.DISCLAIMER)}</p></body></html>"""


def teaser(md: str) -> str:
    """Public part of a Pro report: everything before the second '## ' heading."""
    parts = md.split("\n## ")
    return "\n## ".join(parts[:2]) if len(parts) > 2 else md


def _pro_note() -> str:
    link = config.PRO_CHECKOUT_URL or config.SITE_URL + "/#pricing"
    return f"\n\n**The rest of this playbook is for IOI Pro members.** [Get IOI Pro]({link})"


def _cta() -> str:
    if config.PRO_CHECKOUT_URL:
        return (f'<p class="cta"><strong>Want these signals live?</strong> IOI Pro sends an alert the moment several top '
                f'accounts start on the same topic. <a href="{config.PRO_CHECKOUT_URL}">Get IOI Pro</a></p>')
    if config.FREE_CHANNEL_URL:
        return f'<p class="cta"><strong>Get this every morning:</strong> <a href="{config.FREE_CHANNEL_URL}">join the free Telegram channel</a>.</p>'
    return ""


def _dt(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def build_site() -> dict:
    out = config.PUBLIC_DIR
    (out / "daily").mkdir(parents=True, exist_ok=True)
    digests = db.rows("SELECT * FROM digests ORDER BY day DESC LIMIT 60")
    # public outputs only show Pro alerts after the delay (Pro gets them live on Telegram)
    alerts = db.rows("SELECT * FROM alerts WHERE created_at <= :late ORDER BY created_at DESC LIMIT 50",
                     late=db.ago(hours=config.TEASER_DELAY_HOURS))
    reports = db.rows("SELECT * FROM reports WHERE kind = 'weekly' ORDER BY created_at DESC LIMIT 20")
    (out / "weekly").mkdir(parents=True, exist_ok=True)
    for r in reports:
        (out / "weekly" / f"{r['id']}.html").write_text(
            _page(r["title"], md_lib.markdown(teaser(r["markdown"]) + _pro_note(), extensions=["tables"])), encoding="utf-8"
        )

    # daily pages + index
    items = []
    for d in digests:
        body = md_lib.markdown(d["markdown"], extensions=["tables"])
        (out / "daily" / f"{d['day']}.html").write_text(_page(d["title"], body), encoding="utf-8")
        items.append(f'<li><a href="{config.SITE_URL}/daily/{d["day"]}.html">{html.escape(d["title"])}</a> <span class="small">{d["day"]}</span></li>')
    latest = md_lib.markdown(digests[0]["markdown"], extensions=["tables"]) if digests else "<p>First edition coming soon.</p>"
    (out / "daily" / "index.html").write_text(
        _page(f"{config.BRAND} - archive", "<h1>All daily issues</h1><ul>" + "".join(items) + "</ul>"), encoding="utf-8"
    )
    (out / "latest.html").write_text(_page(config.BRAND, latest), encoding="utf-8")
    from . import landing

    (out / "index.html").write_text(landing.build(digests[0]["markdown"] if digests else None), encoding="utf-8")
    assets = config.ROOT / "assets"
    if assets.exists():
        (out / "assets").mkdir(exist_ok=True)
        for f in assets.iterdir():
            if f.is_file():
                shutil.copy(f, out / "assets" / f.name)

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
        e.description(md_lib.markdown(teaser(r["markdown"]) + _pro_note()))
        e.pubDate(_dt(r["created_at"]))
    for a in reversed(alerts[:30]):
        e = fg.add_entry()
        e.id(f"{config.SITE_URL}/alerts/{a['id']}")
        e.title(f"Attention surge: {a['topic']} (Pro members got this {config.TEASER_DELAY_HOURS}h earlier)")
        e.link(href=f"{config.SITE_URL}/leaderboard.html")
        e.description(html.escape(a["message"]))
        e.pubDate(_dt(a["created_at"]))
    fg.rss_file(str(out / "feed.xml"), pretty=True)
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return {"digests": len(digests), "alerts": len(alerts), "weekly": len(reports), "leaderboard": len(board), "dir": str(out)}
