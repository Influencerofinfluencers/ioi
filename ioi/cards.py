"""The Designer: branded data cards (PNG) for social posts, rendered with headless Chrome.

Chrome is preinstalled on GitHub's ubuntu runners. No Chrome / any error -> None, and the post
simply goes out without an image (a card must never block a post).
"""
import html
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import config

log = logging.getLogger("ioi.cards")
esc = lambda s: html.escape(str(s))  # noqa: E731

LOGO = ('<svg viewBox="50 110 300 180" width="{w}" height="{h}"><rect x="58" y="118" width="34" height="164" rx="6" fill="#F6F6F2"/>'
        '<rect x="308" y="118" width="34" height="164" rx="6" fill="#F6F6F2"/><circle cx="200" cy="200" r="70" fill="none" '
        'stroke="#F6F6F2" stroke-width="30"/><circle cx="200" cy="200" r="24" fill="#C8FF3D" stroke="#0E1116" stroke-width="5"/></svg>')

BASE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@500;600&family=JetBrains+Mono:wght@600&family=Space+Grotesk:wght@700&display=swap" rel="stylesheet">
<style>html,body{{margin:0;width:{w}px;height:{h}px;overflow:hidden;background:#0E1116;color:#F6F6F2;font-family:Inter,sans-serif}}
.k{{font-family:'JetBrains Mono',monospace;color:#C8FF3D;letter-spacing:.08em;font-size:22px}}
h1{{font-family:'Space Grotesk',sans-serif;margin:0;letter-spacing:-.02em}}
.muted{{color:#A9B0BC}}.foot{{position:absolute;left:70px;right:70px;bottom:44px;display:flex;justify-content:space-between;
font-size:20px;color:#A9B0BC}}.logo{{position:absolute;right:70px;top:56px}}</style></head><body>{body}</body></html>"""


def _chrome() -> str | None:
    return shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")


def render(body: str, w: int = 1200, h: int = 675, name: str = "card") -> Path | None:
    exe = _chrome()
    if not exe:
        return None
    try:
        tmp = Path(tempfile.mkdtemp(prefix="ioi-card-"))
        src, out = tmp / f"{name}.html", tmp / f"{name}.png"
        src.write_text(BASE.format(w=w, h=h, body=body), encoding="utf-8")
        subprocess.run([exe, "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                        f"--window-size={w},{h}", "--virtual-time-budget=5000", f"--screenshot={out}", str(src)],
                       check=True, capture_output=True, timeout=90)
        return out if out.exists() and out.stat().st_size > 5000 else None
    except Exception as e:  # noqa: BLE001
        log.warning("card render failed: %s", e)
        return None


def _foot() -> str:
    site = config.SITE_URL.replace("https://", "")
    return f'<div class="foot"><span>{esc(site)}</span><span>Attention data, not financial advice</span></div>'


def highlight(c: dict, tracked: int) -> Path | None:
    """One topic: how many tracked accounts, who was first, mood."""
    share = min(1.0, c["accounts"] / max(tracked, 1))
    mood = c.get("sentiment") or 0
    mood_txt = "positive" if mood > 0.15 else "negative" if mood < -0.15 else "mixed"
    first_at = (c["first"]["at"] or "")[11:16]
    surge = c.get("surge") or 0
    body = f"""<div style="padding:64px 70px">{LOGO.format(w=150, h=90).replace('<svg', '<svg class="logo"')}
<div class="k">IOI · ATTENTION RIGHT NOW</div>
<h1 style="font-size:120px;margin-top:26px">{esc(c['topic'])}</h1>
<div style="font-size:44px;font-weight:600;margin-top:14px">{c['accounts']} of {tracked} top accounts are talking about it</div>
<div style="height:18px;background:#262B33;border-radius:9px;margin-top:30px;width:1060px"><div style="height:18px;width:{max(4, round(share * 100))}%;background:#C8FF3D;border-radius:9px"></div></div>
<div style="display:flex;gap:60px;margin-top:34px;font-size:30px">
<div><div class="muted" style="font-size:22px">FIRST</div>{esc(c['first']['name'])} · {esc(first_at)} UTC</div>
<div><div class="muted" style="font-size:22px">VS USUAL</div>{surge:.1f}×</div>
<div><div class="muted" style="font-size:22px">MOOD</div>{mood_txt}</div></div></div>{_foot()}"""
    return render(body, name="highlight")


def top50(board: list[dict], week: str) -> Path | None:
    """Weekly ranking card: top 10, top 3 highlighted."""
    rows = "".join(
        f'<div style="display:flex;align-items:center;gap:22px;padding:16px 0;border-top:1px solid #262B33;'
        f'font-size:{36 if i < 3 else 29}px;{"" if i < 3 else "color:#C9CED6"}">'
        f'<span style="font-family:JetBrains Mono,monospace;width:48px;color:{"#C8FF3D" if i < 3 else "#A9B0BC"}">{r["rank"]}</span>'
        f'<span style="flex:1;font-weight:600">{esc(r["name"])}</span>'
        f'<span class="muted" style="font-size:24px;width:280px">{r["first_moves"]} first · {r["echoes"]} echoes</span>'
        f'<span style="font-family:JetBrains Mono,monospace;width:110px;text-align:right;color:#C8FF3D">{r["score"]}</span></div>'
        for i, r in enumerate(board[:10])
    )
    body = f"""<div style="padding:56px 70px">{LOGO.format(w=130, h=78).replace('<svg', '<svg class="logo"')}
<div class="k">IOI TOP 50 · {esc(week)}</div>
<h1 style="font-size:54px;margin:14px 0 26px;max-width:880px">Who really moved crypto this week</h1>{rows}</div>{_foot()}"""
    return render(body, w=1200, h=1080, name="top50")
