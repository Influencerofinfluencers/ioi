"""Command line entry point:  python -m ioi.cli <command>

Commands
  init-db         create tables and load config/accounts.csv
  verify          check every handle, store ids + follower counts
  fetch           fetch new posts (optionally --platform x)
  label           label new posts with the cheap model
  analyze         detect attention surges, send Pro alerts
  digest          write today's digest and post it to the free Telegram channel
  publish         build website, leaderboard and RSS into ./public
  cycle           fetch + label + analyze + Pro access (one round)
  loop            run cycle every --every min for --minutes min (used by GitHub Actions)
  daily           digest + publish + cleanup (once a day)
  social          write + post one highlight on X, Bluesky and the free channel
  pro-check       check bot rights in the Pro channel + Whop access (no personal data)
  weekly          playbook: why the best posts of the week worked (Mondays)
  telegram-login  create TELEGRAM_SESSION (interactive, run once)
"""
import argparse
import json
import logging
import sys
import traceback

from . import db, notify

log = logging.getLogger("ioi.cli")


def _print(obj):
    print(json.dumps(obj, indent=2, default=str))


def cmd_cycle(args):
    from . import fetch, influence, label

    started = db.now()
    f = fetch.fetch_all(args.platform)
    lab = label.label_pending()
    alerts = influence.new_alerts()
    for a in alerts:
        notify.alert(a)
    news_stats = {}
    from . import config as _cfg

    if _cfg.NEWS_ENABLED:
        from . import news

        try:
            news_stats = news.cycle()
            f["x_reads"] = f.get("x_reads", 0) + news_stats.get("x_reads", 0)  # one shared X budget
        except Exception as e:  # noqa: BLE001 - the newswire must never stop the core pipeline
            news_stats = {"error": str(e)[:200]}
    teased = notify.pro_teasers()
    from . import pro

    try:
        pro_stats = pro.sync()
    except Exception as e:  # noqa: BLE001 - Pro access must never stop the data pipeline
        pro_stats = {"error": str(e)[:300]}
        notify.status(f"IOI Pro sync failed: {str(e)[:300]}")
    stats = {"fetch": f, "label": lab, "alerts": [a["topic"] for a in alerts], "teasers": teased, "pro": pro_stats, "news": news_stats}
    db.log_run("cycle", started, stats)
    if f["errors"]:
        notify.status("IOI: fetch errors\n" + "\n".join(f["errors"][:10]))
    if f.get("x_budget_hit"):
        notify.status("IOI: X daily read budget reached - X paused until tomorrow (X_MAX_READS_PER_DAY)")
    return stats


SOCIAL_SLOTS_UTC = ((10, 15), (14, 45), (19, 25))  # ~11:15, 15:45, 20:25 Amsterdam (winter)


def _maybe_social():
    """Highlight posts from inside the loop (GitHub skips cron runs). At most one per slot."""
    from datetime import datetime, timezone

    from . import social

    now = datetime.now(timezone.utc)
    minute_of_day = now.hour * 60 + now.minute
    due = [h * 60 + m for h, m in SOCIAL_SLOTS_UTC if 0 <= minute_of_day - (h * 60 + m) < 60]
    if not due:
        return None
    last = db.rows("SELECT MAX(created_at) AS t FROM social_posts")[0]["t"]
    if last and last >= db.ago(hours=3):
        return None
    return social.run_once()


def _dispatch(workflow: str, inputs: dict | None = None) -> bool:
    import os

    import httpx

    token, repo = os.environ.get("GH_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not (token and repo):
        return False
    r = httpx.post(f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
                   headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                   json={"ref": "main", **({"inputs": inputs} if inputs else {})}, timeout=30)
    return r.status_code == 204


def _maybe_scheduled_jobs():
    """Daily digest after 05:41 UTC, weekly playbook Mondays after 06:23 UTC - started from the loop,
    once per day/week (kv marks), so a skipped GitHub cron never means a missing issue."""
    from datetime import datetime, timezone

    from . import pro

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    mins = now.hour * 60 + now.minute
    started = []
    if mins >= 5 * 60 + 41 and pro._kv("daily_started") != today and not db.rows(
            "SELECT day FROM digests WHERE day = :d", d=today):
        if _dispatch("daily.yml"):
            pro._kv("daily_started", today)
            started.append("daily")
    hour_mark = now.strftime("%Y-%m-%dT%H")
    if "daily" not in started and pro._kv("site_refreshed") != hour_mark and now.minute >= 20:
        if _dispatch("daily.yml", {"publish_only": "true"}):  # website + feed every hour
            pro._kv("site_refreshed", hour_mark)
            started.append("site")
    if now.weekday() == 0 and mins >= 6 * 60 + 23 and pro._kv("weekly_started") != today:
        if _dispatch("weekly.yml"):
            pro._kv("weekly_started", today)
            started.append("weekly")
    return started


SCOUT_SLOTS_UTC = ((8, 30), (13, 0), (17, 30))  # ~09:30, 14:00, 18:30 Amsterdam (winter)


def _maybe_marketing():
    """Reply suggestions 3x a day, Top 50 card on Mondays - each at most once (kv marks)."""
    from datetime import datetime, timezone

    from . import pro, scout, social

    now = datetime.now(timezone.utc)
    mins, today, done = now.hour * 60 + now.minute, now.strftime("%Y-%m-%d"), []
    for h, m in SCOUT_SLOTS_UTC:
        mark = f"scout-{today}-{h}"
        if 0 <= mins - (h * 60 + m) < 60 and pro._kv(mark) != "1":
            pro._kv(mark, "1")
            done.append(("scout", scout.run_once()))
    week = now.strftime("%G-%V")
    if now.weekday() == 0 and mins >= 9 * 60 + 5 and pro._kv("top50_week") != week:
        pro._kv("top50_week", week)
        done.append(("top50", social.weekly_top50()))
    return done


def cmd_loop(args):
    """Run a cycle every --every minutes for --minutes minutes (GitHub cron is unreliable,
    so one long job keeps the 10-minute rhythm). One failing cycle never stops the loop."""
    import time

    end = time.time() + args.minutes * 60
    every = args.every * 60
    n = 0
    while time.time() < end - 60:
        start = time.time()
        try:
            db.reset_engine()
            st = cmd_cycle(args)
            print(f"cycle {n + 1}: " + json.dumps({"new_posts": st["fetch"]["new_posts"], "errors": len(st["fetch"]["errors"]),
                  "labeled": st["label"].get("labeled"), "alerts": st["alerts"], "pro": st.get("pro"),
                  "news": st.get("news")}), flush=True)
        except Exception as e:  # noqa: BLE001
            notify.status(f"IOI cycle in loop FAILED: {str(e)[:400]}")
        try:
            _maybe_scheduled_jobs()
        except Exception as e:  # noqa: BLE001
            notify.status(f"IOI could not start daily/weekly: {str(e)[:300]}")
        try:
            _maybe_marketing()
        except Exception as e:  # noqa: BLE001
            notify.status(f"IOI marketing bot FAILED: {str(e)[:400]}")
        try:
            _maybe_social()
        except Exception as e:  # noqa: BLE001
            notify.status(f"IOI social post FAILED: {str(e)[:400]}")
        n += 1
        wait = every - (time.time() - start)
        if time.time() + wait >= end - 60:
            break
        until = time.time() + max(5, wait)
        while time.time() < until:  # buttons + Pro e-mails answered within ~30 s
            try:
                from . import pro

                if until - time.time() > 35:
                    pro.poll()  # long poll ~25 s, answers instantly
                else:
                    time.sleep(max(1, until - time.time()))
            except Exception as e:  # noqa: BLE001
                log.warning("poll failed: %s", e)
                time.sleep(20)
    return {"cycles": n}


def cmd_daily(args):
    from . import config, digest, influence, publish

    started = db.now()
    today = db.iso(db.now())[:10]
    if db.rows("SELECT day FROM digests WHERE day = :d", d=today):
        site = publish.build_site()  # already done today (cron + loop both fired): only refresh the site
        return {"skipped": "digest for today already sent", "site": site}
    purged = db.purge_old(config.RETENTION_DAYS)
    d = digest.build()
    notify.digest(d["markdown"])
    site = publish.build_site()
    top = influence.leaderboard(days=7)[:3]
    runs = db.rows("SELECT stats FROM runs WHERE kind = 'cycle' AND started >= :s", s=db.ago(hours=24))
    new_posts = sum(json.loads(r["stats"])["fetch"]["new_posts"] for r in runs)
    warnings = []
    if len(runs) < 48:  # ~144 expected at every 10 min; far fewer means GitHub skipped runs
        warnings.append(f"only {len(runs)} cycle runs in 24h (expected ~144)")
    if new_posts == 0:
        warnings.append("NO new posts in 24h - check API keys / credits")
    notify.status(
        ("IOI daily OK" if not warnings else "IOI daily - ATTENTION: " + "; ".join(warnings))
        + f"\nDigest: {d['title']}\nNew posts 24h: {new_posts} in {len(runs)} runs"
        + f"\nX reads today: {db.x_reads_today()} · cleaned up: {purged} old posts"
        + "\nTop 3: " + ", ".join(r["name"] for r in top)
    )
    stats = {"digest": d["title"], "site": site, "purged": purged, "warnings": warnings}
    db.log_run("daily", started, stats)
    return stats


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="ioi", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command")
    p.add_argument("--platform", default=None)
    p.add_argument("--minutes", type=int, default=340)
    p.add_argument("--every", type=int, default=10)
    args = p.parse_args(argv)
    c = args.command

    if c == "telegram-login":
        from .sources import telegram

        telegram.login()
        return 0
    try:
        db.init_db()  # cheap, idempotent: also re-syncs accounts.csv
        if c == "init-db":
            _print({"ok": True, "accounts": db.rows("SELECT COUNT(*) AS n FROM accounts")[0]["n"]})
        elif c == "verify":
            from . import fetch

            rep = fetch.verify()
            _print(rep)
            if rep["failed"]:
                notify.status("IOI verify - check these handles:\n" + "\n".join(rep["failed"]))
        elif c == "fetch":
            from . import fetch

            _print(fetch.fetch_all(args.platform))
        elif c == "label":
            from . import label

            _print(label.label_pending())
        elif c == "analyze":
            from . import influence

            alerts = influence.new_alerts()
            for a in alerts:
                notify.alert(a)
            _print({"alerts": alerts, "consensus": influence.consensus()[:5]})
        elif c == "digest":
            from . import digest

            d = digest.build()
            notify.digest(d["markdown"])
            print(d["markdown"])
        elif c == "publish":
            from . import publish

            _print(publish.build_site())
        elif c == "loop":
            _print(cmd_loop(args))
        elif c == "cycle":
            _print(cmd_cycle(args))
        elif c == "daily":
            _print(cmd_daily(args))
        elif c == "llm-check":
            from . import config, llm

            res = {}
            chain = [(config.WRITE_PROVIDER, config.WRITE_MODEL)] + [("openai", m) for m in config.LLM_FALLBACK_MODELS]
            for prov, mdl in chain:
                try:
                    out = llm._one("Reply with the single word OK.", "ping", mdl, 400, prov)
                    res[mdl] = "ok" if out and out.strip() else "empty"
                except Exception as e:  # noqa: BLE001
                    res[mdl] = "fail: " + str(e)[:120]
            notify.status("IOI AI check:\n" + "\n".join(f"{k}: {v}" for k, v in res.items()))
            _print(res)
        elif c == "pro-check":
            from . import pro

            _print(pro.check())
        elif c == "social":
            from . import social

            _print(social.run_once())
        elif c == "top50-post":
            from . import social

            _print(social.weekly_top50())
        elif c == "card-test":
            from . import cards, config, influence

            cs = influence.consensus(hours=24, top=1)
            tracked = db.rows("SELECT COUNT(*) AS n FROM accounts WHERE active = 1")[0]["n"]
            img = cards.highlight(cs[0], tracked) if cs else None
            ok = bool(img) and notify.photo(config.TELEGRAM_STATUS_CHAT_ID, img, "IOI card test (only you see this)")
            _print({"rendered": bool(img), "sent": ok})
        elif c == "social-status":
            rows = db.rows("SELECT created_at, topic, results FROM social_posts ORDER BY created_at DESC LIMIT 5")
            from . import config
            if rows and config.X_BEARER_TOKEN:  # one cheap read: does the newest X post carry the card?
                import httpx

                xurl = json.loads(rows[0]["results"] or "{}").get("x", "")
                if "/status/" in xurl:
                    r = httpx.get(f"{config.X_API_BASE}/tweets/{xurl.rsplit('/', 1)[1]}",
                                  params={"expansions": "attachments.media_keys", "media.fields": "type"},
                                  headers={"Authorization": f"Bearer {config.X_BEARER_TOKEN}"}, timeout=20)
                    rows[0]["x_media"] = (r.json().get("includes") or {}).get("media") if r.status_code == 200 \
                        else f"{r.status_code} {r.text[:120]}"
            _print(rows)
        elif c == "news-check":
            from . import news

            f = news.fetch_all()
            per = {r["source"]: r["n"] for r in db.rows(
                "SELECT source, COUNT(*) AS n FROM news WHERE fetched_at >= :s GROUP BY source", s=db.ago(hours=48))}
            report = {s["key"]: per.get(s["key"], 0) for s in news.SOURCES}
            notify.status("IOI newswire check (items per source, 48h):\n" + "\n".join(f"{k}: {v}" for k, v in report.items())
                          + ("\nERRORS:\n" + "\n".join(f["errors"]) if f["errors"] else ""))
            # re-label the newest 20 so the check always shows teasers from the current prompt
            db.run("""UPDATE news SET labeled = 0 WHERE id IN (SELECT id FROM news ORDER BY fetched_at DESC LIMIT 20)""")
            lab = news.label_pending(limit=40)
            samples = db.rows("""SELECT name, impact, story, teaser FROM news WHERE teaser IS NOT NULL AND teaser != ''
                                 AND labeled = 1 ORDER BY fetched_at DESC, impact DESC LIMIT 12""")
            _print({"per_source": report, "errors": f["errors"], "label": lab, "samples": samples})
        elif c == "scout":
            from . import scout

            _print(scout.run_once())
        elif c == "weekly":
            from . import publish, weekly

            from . import config

            if db.rows("SELECT id FROM reports WHERE kind = 'weekly' AND created_at >= :s", s=db.ago(hours=20)):
                _print({"skipped": "weekly playbook already sent today"})
                return 0
            r = weekly.build()

            notify.send(config.TELEGRAM_PRO_CHAT_ID, notify.md_to_telegram(r["markdown"]))  # full: Pro only
            notify.digest(publish.teaser(r["markdown"]) + publish._pro_note())  # free channel: teaser
            publish.build_site()
            print(r["markdown"])
        else:
            p.print_help()
            return 2
    except Exception as e:  # noqa: BLE001 - always tell the owner on Telegram
        hint = ""
        if "password authentication failed" in str(e):
            hint = "\nHINT: DATABASE_URL has a wrong password (check [YOUR-PASSWORD] was replaced, no brackets)."
        notify.status(f"IOI {c} FAILED: {str(e)[:500]}{hint}\n{traceback.format_exc()[-1000:]}")
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
