"""Command line entry point:  python -m ioi.cli <command>

Commands
  init-db         create tables and load config/accounts.csv
  verify          check every handle, store ids + follower counts
  fetch           fetch new posts (optionally --platform x)
  label           label new posts with the cheap model
  analyze         detect attention surges, send Pro alerts
  digest          write today's digest and post it to the free Telegram channel
  publish         build website, leaderboard and RSS into ./public
  cycle           fetch + label + analyze   (every 30 minutes)
  daily           digest + publish + cleanup (once a day)
  weekly          playbook: why the best posts of the week worked (Mondays)
  telegram-login  create TELEGRAM_SESSION (interactive, run once)
"""
import argparse
import json
import logging
import sys
import traceback

from . import db, notify


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
    stats = {"fetch": f, "label": lab, "alerts": [a["topic"] for a in alerts]}
    db.log_run("cycle", started, stats)
    if f["errors"]:
        notify.status("IOI: fetch errors\n" + "\n".join(f["errors"][:10]))
    if f.get("x_budget_hit"):
        notify.status("IOI: X daily read budget reached - X paused until tomorrow (X_MAX_READS_PER_DAY)")
    return stats


def cmd_daily(args):
    from . import config, digest, influence, publish

    started = db.now()
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
    args = p.parse_args(argv)
    c = args.command

    if c == "telegram-login":
        from .sources import telegram

        telegram.login()
        return 0
    db.init_db()  # cheap, idempotent: also re-syncs accounts.csv
    try:
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
        elif c == "cycle":
            _print(cmd_cycle(args))
        elif c == "daily":
            _print(cmd_daily(args))
        elif c == "weekly":
            from . import publish, weekly

            r = weekly.build()
            notify.digest(r["markdown"])
            publish.build_site()
            print(r["markdown"])
        else:
            p.print_help()
            return 2
    except Exception as e:  # noqa: BLE001 - always tell the owner on Telegram
        notify.status(f"IOI {c} FAILED: {e}\n{traceback.format_exc()[-1500:]}")
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
