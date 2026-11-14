"""Database layer. Works with SQLite (local tests) and Postgres (Supabase)."""
import csv
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text

from . import config

_engine = None

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS accounts (
        key TEXT PRIMARY KEY,
        name TEXT, category TEXT, platform TEXT, handle TEXT,
        active INTEGER DEFAULT 1,
        platform_user_id TEXT,
        followers INTEGER,
        last_seen_id TEXT,
        last_checked TEXT,
        error TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS posts (
        id TEXT PRIMARY KEY,
        account_key TEXT, platform TEXT, url TEXT, text TEXT,
        created_at TEXT, fetched_at TEXT,
        likes INTEGER DEFAULT 0, replies INTEGER DEFAULT 0,
        reposts INTEGER DEFAULT 0, views INTEGER DEFAULT 0,
        labeled INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS labels (
        post_id TEXT PRIMARY KEY,
        topics TEXT, category TEXT, sentiment REAL, relevance INTEGER, summary TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS alerts (
        id TEXT PRIMARY KEY,
        topic TEXT, created_at TEXT, accounts TEXT, message TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS digests (
        day TEXT PRIMARY KEY, title TEXT, markdown TEXT, created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY, kind TEXT, started TEXT, finished TEXT, stats TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS reports (
        id TEXT PRIMARY KEY, kind TEXT, title TEXT, markdown TEXT, created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS social_posts (
        id TEXT PRIMARY KEY, created_at TEXT, topic TEXT, text TEXT, results TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)""",
    """CREATE TABLE IF NOT EXISTS news (
        id TEXT PRIMARY KEY, source TEXT, name TEXT, kind TEXT, title TEXT, url TEXT,
        published_at TEXT, fetched_at TEXT, labeled INTEGER DEFAULT 0,
        topics TEXT, impact INTEGER, teaser TEXT, alerted TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS news_published ON news (published_at)",
    """CREATE TABLE IF NOT EXISTS pro_members (
        membership_id TEXT PRIMARY KEY, email TEXT, tg_user_id TEXT, status TEXT, invited_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS posts_created ON posts (created_at)",
    "CREATE INDEX IF NOT EXISTS posts_labeled ON posts (labeled)",
]


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    """Uniform UTC timestamp string, so text comparison equals time comparison."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ago(**kw) -> str:
    return iso(now() - timedelta(**kw))


def engine():
    global _engine
    if _engine is None:
        url = config.DATABASE_URL
        if url.startswith("postgres://"):  # Supabase/Heroku style
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        if url.startswith("postgresql"):
            # Supabase session pooler allows 15 clients in total, shared by every run:
            # keep this process to at most 2 connections and recycle them.
            _engine = create_engine(url, pool_pre_ping=True, pool_size=1, max_overflow=1, pool_recycle=600)
        else:
            _engine = create_engine(url, pool_pre_ping=True)
    return _engine


def reset_engine():
    """Close every pooled connection before dropping the engine (otherwise each loop
    cycle leaked an idle connection until the pooler was full)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def run(sql: str, **params):
    with engine().begin() as conn:
        return conn.execute(text(sql), params)


def rows(sql: str, **params) -> list[dict]:
    with engine().begin() as conn:
        return [dict(r._mapping) for r in conn.execute(text(sql), params)]


MIGRATIONS = [  # columns added after v0.1; ignored when they already exist
    "ALTER TABLE labels ADD COLUMN format TEXT",
    "ALTER TABLE labels ADD COLUMN hook TEXT",
    "ALTER TABLE runs ADD COLUMN x_reads INTEGER DEFAULT 0",
    "ALTER TABLE alerts ADD COLUMN teased_at TEXT",
    "ALTER TABLE news ADD COLUMN story TEXT",
]


def init_db():
    with engine().begin() as conn:
        for stmt in SCHEMA:
            conn.execute(text(stmt))
    for stmt in MIGRATIONS:
        try:
            with engine().begin() as conn:
                conn.execute(text(stmt))
        except Exception:  # noqa: BLE001 - column exists
            pass
    sync_accounts()


def purge_old(days: int) -> int:
    """Delete posts (and their labels) older than `days`. Keeps the database small and
    honours the promise to keep data no longer than needed."""
    cutoff = ago(days=days)
    run("DELETE FROM news WHERE published_at < :c", c=cutoff)
    run("DELETE FROM labels WHERE post_id IN (SELECT id FROM posts WHERE created_at < :c)", c=cutoff)
    return run("DELETE FROM posts WHERE created_at < :c", c=cutoff).rowcount or 0


def sync_accounts():
    """Load config/accounts.csv into the accounts table (keeps fetch state)."""
    keys = []
    with open(config.ACCOUNTS_CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            keys.append(r["key"].strip())
            run(
                """INSERT INTO accounts (key, name, category, platform, handle, active)
                   VALUES (:key, :name, :category, :platform, :handle, :active)
                   ON CONFLICT (key) DO UPDATE SET
                     platform_user_id = CASE WHEN accounts.platform = excluded.platform
                                             AND accounts.handle = excluded.handle
                                             THEN accounts.platform_user_id ELSE NULL END,
                     last_seen_id = CASE WHEN accounts.platform = excluded.platform
                                         AND accounts.handle = excluded.handle
                                         THEN accounts.last_seen_id ELSE NULL END,
                     name = excluded.name, category = excluded.category,
                     platform = excluded.platform, handle = excluded.handle,
                     active = excluded.active""",
                key=r["key"].strip(),
                name=r["name"].strip(),
                category=r["category"].strip(),
                platform=r["platform"].strip().lower(),
                handle=r["handle"].strip(),
                active=int(r.get("active", "1") or 1),
            )
    # accounts removed from the CSV are switched off (history is kept)
    if keys:
        params = {f"k{i}": k for i, k in enumerate(keys)}
        run(f"UPDATE accounts SET active = 0 WHERE key NOT IN ({', '.join(':' + p for p in params)})", **params)


def upsert_post(p: dict) -> bool:
    """Insert a post once; refresh engagement numbers if we see it again."""
    res = run(
        """INSERT INTO posts (id, account_key, platform, url, text, created_at, fetched_at,
                              likes, replies, reposts, views)
           VALUES (:id, :account_key, :platform, :url, :text, :created_at, :fetched_at,
                   :likes, :replies, :reposts, :views)
           ON CONFLICT (id) DO UPDATE SET
             likes = excluded.likes, replies = excluded.replies,
             reposts = excluded.reposts, views = excluded.views""",
        fetched_at=iso(now()),
        **{k: p.get(k, 0) for k in ("likes", "replies", "reposts", "views")},
        **{k: p[k] for k in ("id", "account_key", "platform", "url", "text", "created_at")},
    )
    return res.rowcount > 0


def log_run(kind: str, started: datetime, stats: dict):
    run(
        "INSERT INTO runs (id, kind, started, finished, stats, x_reads) VALUES (:id, :k, :s, :f, :st, :xr)",
        id=f"{kind}-{iso(started)}",
        k=kind,
        s=iso(started),
        f=iso(now()),
        st=json.dumps(stats),
        xr=int(stats.get("fetch", {}).get("x_reads", 0)) if isinstance(stats.get("fetch"), dict) else 0,
    )


def x_reads_today() -> int:
    day = iso(now())[:10]
    r = rows("SELECT COALESCE(SUM(x_reads), 0) AS n FROM runs WHERE started >= :d", d=day)
    return int(r[0]["n"] or 0)
