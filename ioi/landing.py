"""The public landing page (index.html), built from live data every day.

Buttons only appear when their link is configured (PRO_CHECKOUT_URL, FREE_CHANNEL_URL, ...),
so the page never shows a dead button.
"""
import html
from string import Template

import markdown as md_lib

from . import config, db, influence

esc = lambda s: html.escape(str(s), quote=True)  # noqa: E731

LOGO = ('<svg viewBox="50 110 300 180" width="44" height="26" aria-hidden="true" xmlns="http://www.w3.org/2000/svg">'
        '<rect x="58" y="118" width="34" height="164" rx="6" fill="#0B0D12"/><rect x="308" y="118" width="34" height="164" rx="6" fill="#0B0D12"/>'
        '<circle cx="200" cy="200" r="70" fill="none" stroke="#0B0D12" stroke-width="30"/>'
        '<circle cx="200" cy="200" r="24" fill="#C8FF3D" stroke="#0B0D12" stroke-width="5"/></svg>')

CSS = """
*{box-sizing:border-box}body{margin:0;background:#F6F6F2;color:#0E1116;font-family:Inter,system-ui,sans-serif}
a{color:#0B0D12}a:focus-visible,button:focus-visible{outline:3px solid #0B0D12;outline-offset:2px}
.wrap{max-width:1200px;margin:0 auto;padding:0 24px}
h1,h2,h3{font-family:'Space Grotesk',sans-serif;letter-spacing:-.01em}
.mono{font-family:'JetBrains Mono',monospace}
.kicker{margin:0;font-family:'JetBrains Mono',monospace;font-size:13px;letter-spacing:.06em;text-transform:uppercase}
.muted{color:#3A3F47}.soft{color:#5B616B}
.btn{display:inline-flex;align-items:center;justify-content:center;min-height:48px;padding:0 24px;border-radius:999px;
font-weight:600;text-decoration:none;font-size:16px;border:0}
.btn.dark{background:#0E1116;color:#F6F6F2}.btn.lime{background:#C8FF3D;color:#0B0D12}
.btn.ghost{border:1.5px solid #0E1116;color:#0E1116;background:transparent}
.btn.off{background:#E2E2DC;color:#5B616B;cursor:default}
header.top{border-bottom:1px solid #E2E2DC}
header.top .wrap{display:flex;flex-wrap:wrap;gap:16px;align-items:center;justify-content:space-between;padding-top:18px;padding-bottom:18px}
.brand{display:flex;align-items:center;gap:10px;text-decoration:none;font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:18px}
nav.main{display:flex;flex-wrap:wrap;gap:22px;align-items:center;font-size:15px}nav.main a{color:#3A3F47;text-decoration:none}
.hero{display:flex;flex-wrap:wrap;gap:48px;align-items:center;padding-top:72px;padding-bottom:56px}
.hero>div{flex:1 1 520px;min-width:0;display:flex;flex-direction:column;gap:24px}
.hero h1{margin:0;font-size:clamp(38px,5.4vw,64px);line-height:1.04;letter-spacing:-.02em}
.hero p.lead{margin:0;font-size:19px;line-height:1.55;max-width:560px}
.ctas{display:flex;flex-wrap:wrap;gap:10px}
.card-dark{flex:1 1 400px;min-width:0;background:#0E1116;color:#F6F6F2;border-radius:20px;padding:24px;display:flex;flex-direction:column;gap:16px}
.bar{height:8px;border-radius:4px;background:#262B33;overflow:hidden}.bar>div{height:100%;background:#C8FF3D;border-radius:4px}
.strip{border-top:1px solid #E2E2DC;border-bottom:1px solid #E2E2DC;background:#fff}
.strip .wrap{padding-top:20px;padding-bottom:20px;display:flex;flex-wrap:wrap;gap:12px 32px;justify-content:center;font-size:15px;color:#3A3F47}
section.block{padding-top:88px;padding-bottom:88px}
.block h2{margin:0;font-size:40px}
.steps{display:flex;flex-wrap:wrap;gap:20px;margin-top:40px}
.step{background:#fff;border:1px solid #E2E2DC;border-radius:16px;padding:28px;flex:1 1 280px;min-width:0;display:flex;flex-direction:column;gap:12px}
.tag{font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:600;background:#C8FF3D;align-self:flex-start;padding:2px 8px;border-radius:4px}
.dark{background:#0E1116;color:#F6F6F2}
.split{display:flex;flex-wrap:wrap;gap:48px;align-items:flex-start}
.split>.l{flex:1 1 360px;min-width:0;display:flex;flex-direction:column;gap:16px}.split>.r{flex:1.4 1 520px;min-width:0}
table{width:100%;border-collapse:collapse;font-size:15px}
.tbl{overflow-x:auto;border:1px solid #262B33;border-radius:16px}
.tbl th{color:#A9B0BC;font-weight:500;font-size:13px;text-align:left;padding:10px 18px}
.tbl td{padding:14px 18px;border-top:1px solid #262B33}
.issue{background:#fff;border:1px solid #E2E2DC;border-radius:16px;padding:32px;line-height:1.6}
.issue h1{font-size:28px;line-height:1.15;margin:0 0 12px}.issue h2{font-size:15px!important;text-transform:uppercase;letter-spacing:.05em;margin:24px 0 8px!important;font-family:Inter,sans-serif}
.plans{display:flex;flex-wrap:wrap;gap:20px;margin-top:40px}
.plan{border-radius:16px;padding:28px;flex:1 1 280px;min-width:0;display:flex;flex-direction:column;gap:14px;background:#F6F6F2;border:1px solid #E2E2DC}
.plan.pro{background:#0E1116;color:#F6F6F2;border-color:#0E1116}
.plan .price{font-family:'Space Grotesk',sans-serif;font-size:40px;font-weight:700}
.plan ul{margin:0;padding-left:18px;line-height:1.8;font-size:15px}.plan .btn{margin-top:auto}
.pill{font-size:12px;font-weight:600;background:#C8FF3D;color:#0B0D12;padding:4px 10px;border-radius:999px}
details{border-top:1px solid #E2E2DC;padding:16px 0}summary{cursor:pointer;font-family:'Space Grotesk',sans-serif;font-size:20px;font-weight:700;min-height:44px;display:flex;align-items:center}
details p{font-size:16px;line-height:1.6;color:#3A3F47;margin:8px 0 0}
.final{background:#0B0D12;color:#F6F6F2;border-radius:24px;padding:56px 40px;display:flex;flex-wrap:wrap;gap:24px;align-items:center;justify-content:space-between}
footer{border-top:1px solid #E2E2DC}footer .wrap{padding-top:32px;padding-bottom:32px;display:flex;flex-wrap:wrap;gap:16px 32px;justify-content:space-between;font-size:14px;color:#5B616B}
footer a{color:#5B616B}
"""

PAGE = Template("""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IOI – Influencer of Influencers · who moves crypto, and who said it first</title>
<meta name="description" content="Every morning: what crypto's 50 most influential voices are saying, where they agree, and who said it first. Free daily brief, live alerts for Pro.">
<meta property="og:title" content="IOI – Influencer of Influencers"><meta property="og:description" content="What crypto's 50 most influential voices say. And who said it first.">
<meta property="og:image" content="$site/assets/IOI-X-banner.png"><meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="$site/assets/IOI-favicon.ico">
<link rel="alternate" type="application/rss+xml" title="IOI" href="$site/feed.xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;600&family=Space+Grotesk:wght@500;700&display=swap" rel="stylesheet">
<style>$css</style></head><body>
<header class="top"><div class="wrap">
<a class="brand" href="$site/">$logo<span>IOI <span style="font-weight:500;color:#3A3F47">· Influencer of Influencers</span></span></a>
<nav class="main" aria-label="Main"><a href="#how">How it works</a><a href="#top50">Top 50</a><a href="#sample">Today's issue</a><a href="#pricing">Pricing</a>$nav_cta</nav>
</div></header>

<section class="wrap hero" id="top"><div>
<p class="kicker">Crypto attention intelligence · free daily briefing</p>
<h1>What crypto's $n most influential voices are saying. And who said it first.</h1>
<p class="lead muted">Every morning, one 3-minute read: the topics the top accounts agree on, the account that moved first, and links to every source. Plain English, no hype, no shilling.</p>
<div class="ctas">$hero_ctas</div>
<p class="soft" style="margin:0;font-size:14px">Free forever · No account needed · Every source linked</p>
</div>
<aside class="card-dark" aria-label="Attention right now">
<div style="display:flex;justify-content:space-between;align-items:baseline;gap:12px"><h2 style="margin:0;font-size:20px">Attention right now</h2>
<span class="mono" style="font-size:12px;color:#A9B0BC">LIVE · last 24h</span></div>
$attention
<p style="margin:0;font-size:12px;color:#A9B0BC;border-top:1px solid #262B33;padding-top:12px">Bars = share of the $n tracked accounts talking about the topic. Updated $updated. Pro members get surges live, with alerts.</p>
</aside></section>

<section class="strip" aria-label="Sources"><div class="wrap"><span style="font-weight:600;color:#0E1116">$n voices tracked 24/7 on</span>$platforms</div></section>

<section class="wrap block" id="how">
<div style="max-width:720px;display:flex;flex-direction:column;gap:12px"><h2>Follower counts lie. First movers don't.</h2>
<p class="muted" style="margin:0;font-size:18px;line-height:1.55">Anyone can buy followers. What you can't fake is raising a topic before everyone else picks it up. IOI measures exactly that.</p></div>
<div class="steps">
<div class="step"><span class="tag">01 · TRACK</span><h3 style="margin:0;font-size:22px">$n voices, every platform</h3><p class="muted" style="margin:0;line-height:1.55">Founders, analysts, on-chain sleuths and newsrooms on X, YouTube and Telegram. Checked every 10 minutes, day and night.</p></div>
<div class="step"><span class="tag">02 · DETECT</span><h3 style="margin:0;font-size:22px">Who said it first</h3><p class="muted" style="margin:0;line-height:1.55">AI tags every post by topic and mood. We see who raised a topic first, who followed, and where the top accounts agree.</p></div>
<div class="step"><span class="tag">03 · BRIEF</span><h3 style="margin:0;font-size:22px">One read, every morning</h3><p class="muted" style="margin:0;line-height:1.55">A 3-minute summary in plain English with a link to every source. Pro members get the signals the moment they happen.</p></div>
</div></section>

<section class="dark" id="top50"><div class="wrap block split">
<div class="l"><p class="kicker" style="color:#C8FF3D">Updated every day</p><h2>The IOI Top 50</h2>
<p style="margin:0;font-size:18px;line-height:1.55;color:#C9CED6">Who really moves the crypto conversation this week. 3 points for every topic an account raised first that others then picked up, 1 point for every account that echoed them within 24 hours. Followers don't count.</p>
<a class="btn" style="align-self:flex-start;background:#F6F6F2;color:#0E1116" href="$site/leaderboard.html">See the full ranking</a></div>
<div class="r tbl"><table><thead><tr><th>#</th><th>Account</th><th>Platform</th><th>First moves</th><th>Echoes</th><th>Score</th></tr></thead><tbody>$board</tbody></table></div>
</div></section>

<section class="wrap block split" id="sample">
<div class="l"><h2>Today's issue</h2>
<p class="muted" style="margin:0;font-size:18px;line-height:1.55">Three minutes, three parts: what the top accounts agree on, who moved first, and quick hits. Every claim links to its source.</p>
<ul class="muted" style="margin:0;padding-left:20px;line-height:1.8"><li>Written fresh every morning, 06:40 CET</li><li>Summaries in our own words, never reposts</li><li>Free on Telegram and RSS</li></ul>
<a class="btn ghost" style="align-self:flex-start" href="$site/daily/">All past issues</a></div>
<article class="r issue" aria-label="Latest issue">$issue</article>
</section>

<section style="background:#fff;border-top:1px solid #E2E2DC" id="pricing"><div class="wrap block">
<div style="max-width:720px;display:flex;flex-direction:column;gap:12px"><h2>Start free. Go live when it pays.</h2>
<p class="muted" style="margin:0;font-size:18px;line-height:1.55">The daily brief is free forever. Pro gives you the same signals in real time, before they reach the free channel.</p></div>
<div class="plans">
<div class="plan"><h3 style="margin:0;font-size:22px">Free</h3><p style="margin:0"><span class="price">€0</span> <span class="soft">forever</span></p>
<p class="soft" style="margin:0;font-size:15px">For anyone who wants the signal without the noise.</p>
<ul><li>Daily brief on Telegram</li><li>RSS feed</li><li>IOI Top 50, updated daily</li><li>Pro alerts, a few hours later</li></ul>$free_btn</div>
<div class="plan pro"><div style="display:flex;justify-content:space-between;align-items:center"><h3 style="margin:0;font-size:22px">Pro</h3><span class="pill">Most popular</span></div>
<p style="margin:0"><span class="price">$pro_price</span> <span style="color:#C9CED6">/ month</span></p>
<p style="margin:0;font-size:15px;color:#C9CED6">For active traders and writers who want it first.</p>
<ul><li>Real-time attention alerts (checked every 10 min)</li><li>Consensus and first-mover signals</li><li>Full weekly playbook</li><li>Cancel anytime</li></ul>$pro_btn</div>
<div class="plan"><h3 style="margin:0;font-size:22px">Data API</h3><p style="margin:0"><span class="price">from €99</span> <span class="soft">/ month</span></p>
<p class="soft" style="margin:0;font-size:15px">For bots, dashboards and funds.</p>
<ul><li>JSON feeds and webhooks</li><li>Topic surges and first movers</li><li>Historical data</li><li>Commercial use</li></ul>$api_btn</div>
</div></div></section>

<section class="wrap block" id="faq" style="max-width:860px"><h2 style="margin-bottom:16px">Questions</h2>
<details open><summary>Is this financial advice?</summary><p>No. IOI measures what influential accounts talk about and how they feel about it. We never tell you to buy or sell anything. Always do your own research.</p></details>
<details><summary>Where does the data come from?</summary><p>Only public posts, collected through the official APIs of X, YouTube and Telegram. We summarise in our own words and always link to the original.</p></details>
<details><summary>How is the Top 50 scored?</summary><p>3 points for every topic an account raised first that others then picked up, 1 point for every tracked account that echoed them within 24 hours. Follower counts do not count.</p></details>
<details><summary>What do I get with Pro?</summary><p>A private Telegram channel that pings you the moment several top accounts suddenly start on the same topic, with who was first and a link. The free channel only sees these hours later.</p></details>
<details><summary>Can I cancel Pro anytime?</summary><p>Yes, in one click. You keep access until the end of the period you paid for.</p></details>
</section>

<section class="wrap" style="padding-bottom:88px"><div class="final">
<div style="flex:1 1 420px;min-width:0"><h2 style="margin:0 0 10px;font-size:36px">Know who moves the market before the market moves.</h2>
<p style="margin:0;font-size:17px;color:#C9CED6">Free daily brief. 3 minutes. Every source linked.</p></div>$final_cta</div></section>

<footer><div class="wrap"><p style="margin:0;max-width:620px;line-height:1.6">$disclaimer</p>
<nav aria-label="Footer" style="display:flex;flex-wrap:wrap;gap:20px">$footer_links</nav></div></footer>
</body></html>""")


def _btn(url: str, label: str, cls: str = "dark", fallback: str = "Coming soon") -> str:
    if url:
        return f'<a class="btn {cls}" href="{esc(url)}" rel="noopener">{esc(label)}</a>'
    return f'<span class="btn off" aria-disabled="true">{esc(fallback)}</span>'


def build(latest_digest_md: str | None) -> str:
    site = config.SITE_URL
    n = db.rows("SELECT COUNT(*) AS n FROM accounts WHERE active = 1")[0]["n"] or 50
    order = ["x", "youtube", "telegram", "bluesky", "instagram"]
    platforms = sorted((r["platform"] for r in db.rows("SELECT DISTINCT platform FROM accounts WHERE active = 1")),
                       key=lambda p: order.index(p) if p in order else 99)
    names = {"x": "X", "youtube": "YouTube", "telegram": "Telegram", "bluesky": "Bluesky", "instagram": "Instagram"}

    att = influence.consensus(hours=24, top=5)
    if att:
        top_share = max(c["share"] for c in att) or 1
        attention = "".join(
            f'<div style="display:flex;flex-direction:column;gap:6px"><div style="display:flex;justify-content:space-between;gap:12px;font-size:14px">'
            f'<span class="mono" style="font-weight:600">{esc(c["topic"])}</span><span style="color:#C9CED6">{c["accounts"]} of {n} accounts</span></div>'
            f'<div class="bar"><div style="width:{max(6, round(100 * c["share"] / top_share))}%"></div></div>'
            f'<span style="font-size:12px;color:#A9B0BC">First: {esc(c["first"]["name"])}</span></div>'
            for c in att
        )
    else:
        attention = '<p style="margin:0;color:#C9CED6">Collecting the first signals of the day…</p>'

    board = influence.leaderboard(days=7)[:10]
    rows = "".join(
        f'<tr><td class="mono">{r["rank"]}</td><td style="font-weight:600">{esc(r["name"])}</td><td style="color:#C9CED6">{esc(names.get(r["platform"], r["platform"]))}</td>'
        f'<td class="mono">{r["first_moves"]}</td><td class="mono">{r["echoes"]}</td><td class="mono" style="color:#C8FF3D;font-weight:600">{r["score"]}</td></tr>'
        for r in board
    ) or '<tr><td colspan="6" style="color:#C9CED6">The ranking fills in as the week goes on.</td></tr>'

    issue = md_lib.markdown(latest_digest_md, extensions=["tables"]) if latest_digest_md else "<p>First edition coming soon.</p>"

    free_url = config.FREE_CHANNEL_URL or config.NEWSLETTER_URL
    free_label = "Join free on Telegram" if config.FREE_CHANNEL_URL else "Get the daily brief"
    hero = _btn(free_url, free_label, "dark", "Free channel opening soon") + _btn(f"{site}/feed.xml", "RSS feed", "ghost")
    if config.PRO_CHECKOUT_URL:
        hero += _btn(config.PRO_CHECKOUT_URL, "Get live alerts (Pro)", "lime")
    nav_cta = f'<a class="btn dark" style="min-height:44px;padding:0 18px;font-size:15px;color:#F6F6F2" href="{esc(free_url)}">Subscribe free</a>' if free_url else ""
    pro_btn = _btn(config.PRO_CHECKOUT_URL, "Go Pro", "lime", "Pro opens soon")
    api_btn = _btn(config.CONTACT_URL, "Request access", "dark", "Coming soon")
    final = _btn(free_url, free_label, "lime", "Opening soon")

    links = [("RSS", f"{site}/feed.xml"), ("Telegram", config.FREE_CHANNEL_URL), ("X", config.X_PROFILE_URL),
             ("Bluesky", config.BLUESKY_PROFILE_URL), ("Archive", f"{site}/daily/"), ("Top 50", f"{site}/leaderboard.html")]
    footer_links = "".join(f'<a href="{esc(u)}">{t}</a>' for t, u in links if u)

    return PAGE.substitute(
        site=site, css=CSS, logo=LOGO, n=n, updated=db.iso(db.now())[:16].replace("T", " ") + " UTC",
        nav_cta=nav_cta, hero_ctas=hero, attention=attention,
        platforms="".join(f"<span>{names.get(p, p)}</span>" for p in platforms),
        board=rows, issue=issue, free_btn=_btn(free_url, free_label, "dark", "Opening soon"),
        pro_price=esc(config.PRO_PRICE), pro_btn=pro_btn, api_btn=api_btn, final_cta=final,
        disclaimer=esc(config.DISCLAIMER), footer_links=footer_links,
    )
