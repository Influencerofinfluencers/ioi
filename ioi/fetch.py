"""Fetch new posts for every active account, platform by platform."""
import logging

from . import db
from .sources import ADAPTERS, SourceNotConfigured
from .sources import x as x_source

log = logging.getLogger("ioi.fetch")


def _accounts(platform: str | None = None) -> list[dict]:
    sql = "SELECT * FROM accounts WHERE active = 1"
    if platform:
        sql += " AND platform = :p"
        return db.rows(sql, p=platform)
    return db.rows(sql)


def _save_account(a: dict, error: str | None = None):
    db.run(
        """UPDATE accounts SET platform_user_id = :uid, followers = :f,
               last_seen_id = :last, last_checked = :t, error = :e WHERE key = :k""",
        uid=a.get("platform_user_id"),
        f=a.get("followers"),
        last=a.get("last_seen_id"),
        t=db.iso(db.now()),
        e=error,
        k=a["key"],
    )


def verify() -> dict:
    """Resolve handles -> ids + follower counts. Run once after editing accounts.csv."""
    report = {"ok": [], "failed": [], "skipped": []}
    accounts = _accounts()
    # X: one batched lookup instead of one call per account
    x_accounts = [a for a in accounts if a["platform"] == "x"]
    if x_accounts:
        try:
            found = x_source.resolve_many(x_accounts)
            for a in x_accounts:
                hit = found.get(a["handle"].lstrip("@").lower())
                if hit:
                    a.update(hit)
                    _save_account(a)
                    report["ok"].append(a["key"])
                else:
                    _save_account(a, "not found")
                    report["failed"].append(f"{a['key']}: X user not found")
        except SourceNotConfigured as e:
            report["skipped"] += [f"{a['key']}: {e}" for a in x_accounts]
    for a in accounts:
        if a["platform"] == "x":
            continue
        adapter = ADAPTERS.get(a["platform"])
        if not adapter:
            report["failed"].append(f"{a['key']}: unknown platform {a['platform']}")
            continue
        try:
            a.update(adapter.resolve(a))
            _save_account(a)
            report["ok"].append(a["key"])
        except SourceNotConfigured as e:
            report["skipped"].append(f"{a['key']}: {e}")
        except Exception as e:  # noqa: BLE001 - report and keep going
            _save_account(a, str(e)[:300])
            report["failed"].append(f"{a['key']}: {e}")
    return report


def fetch_all(platform: str | None = None) -> dict:
    from . import config

    stats = {"accounts": 0, "new_posts": 0, "errors": [], "skipped": 0, "x_reads": 0, "x_budget_hit": False}
    x_used = db.x_reads_today()
    for a in _accounts(platform):
        adapter = ADAPTERS.get(a["platform"])
        if not adapter:
            continue
        if a["platform"] == "x" and x_used + stats["x_reads"] >= config.X_MAX_READS_PER_DAY:
            stats["x_budget_hit"] = True  # stop paying for today, try again tomorrow
            continue
        try:
            posts = adapter.fetch(a)
        except SourceNotConfigured:
            stats["skipped"] += 1
            continue
        except Exception as e:  # noqa: BLE001
            log.warning("fetch failed for %s: %s", a["key"], e)
            stats["errors"].append(f"{a['key']}: {str(e)[:150]}")
            _save_account(a, str(e)[:300])
            continue
        if a["platform"] == "x":
            stats["x_reads"] += len(posts)
        for p in posts:
            if not db.rows("SELECT 1 AS x FROM posts WHERE id = :id", id=p["id"]):
                stats["new_posts"] += 1
            db.upsert_post(p)
        newest = adapter.newest_id(posts)
        if newest:
            a["last_seen_id"] = newest
        _save_account(a)
        stats["accounts"] += 1
    return stats
