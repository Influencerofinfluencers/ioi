"""End-to-end test without internet: fake APIs and a fake LLM, real SQLite + logic."""
import json
from datetime import timedelta

import httpx
import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from ioi import config, db

    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path / 't.db'}")
    monkeypatch.setattr(config, "PUBLIC_DIR", tmp_path / "public")
    monkeypatch.setattr(config, "ALERT_MIN_ACCOUNTS", 3)
    db.reset_engine()
    db.init_db()
    yield db
    db.reset_engine()


def fake_complete(system, user, model, max_tokens=2000, provider=None):
    if "You label" in system:
        n = user.count("\n")  # one line per post
        out = []
        for i in range(n):
            line = user.splitlines()[i + 1]
            topics = ["BTC", "ETF"] if "ETF" in line else ["SOL"] if "Solana" in line else ["MEME"]
            out.append({"i": i, "topics": topics, "category": "etf_institutional" if "ETF" in line else "other",
                        "sentiment": 0.5, "relevance": 8 if "ETF" in line else 4, "summary": line[:60]})
        return json.dumps(out)
    data = json.loads(user.split("\n", 1)[1])
    top = data["consensus"][0]
    return f"# {top['topic']} takes over\n\nIntro.\n\n## What the top accounts are talking about\n\n**{top['topic']}** - {top['accounts']} accounts, first: {top['first']['name']} ([source]({top['first']['url']}))\n\n*{'Attention data, not financial advice.'}*"


def seed_posts(db):
    t0 = db.now() - timedelta(hours=5)
    keys = ["balchunas", "seyffart", "wu", "coindesk", "saylor"]
    for i, k in enumerate(keys):  # balchunas first, the others echo within hours
        db.upsert_post({
            "id": f"x:{100 + i}", "account_key": k, "platform": "x",
            "url": f"https://x.com/{k}/status/{100 + i}", "text": f"Big spot BTC ETF inflow news number {i}",
            "created_at": db.iso(t0 + timedelta(minutes=30 * i)), "likes": 100 * (i + 1),
        })
    db.upsert_post({"id": "x:200", "account_key": "toly", "platform": "x", "url": "https://x.com/aeyakovenko/status/200",
                    "text": "Solana validator client update shipping today", "created_at": db.iso(t0), "likes": 50})
    db.upsert_post({"id": "x:201", "account_key": "kaleo", "platform": "x", "url": "u", "text": "gm", "created_at": db.iso(t0)})


def test_pipeline(env, monkeypatch):
    from ioi import digest, influence, label, llm, publish

    db = env
    assert db.rows("SELECT COUNT(*) AS n FROM accounts")[0]["n"] == 50
    seed_posts(db)
    monkeypatch.setattr(llm, "complete", fake_complete)

    stats = label.label_pending()
    assert stats["labeled"] == 6 and stats["skipped_short"] == 1

    cons = influence.consensus()
    assert cons[0]["topic"] in ("BTC", "ETF") and cons[0]["accounts"] == 5
    assert cons[0]["first"]["name"] == "Eric Balchunas"

    board = influence.leaderboard()
    assert board[0]["key"] == "balchunas" and board[0]["first_moves"] >= 1 and board[0]["echoes"] == 4

    alerts = influence.new_alerts()
    assert {a["topic"] for a in alerts} == {"BTC", "ETF"}
    assert influence.new_alerts() == []  # no duplicate alert within 24h

    d = digest.build()
    assert "Eric Balchunas" in d["markdown"]

    site = publish.build_site()
    from ioi import config
    feed = (config.PUBLIC_DIR / "feed.xml").read_text()
    assert "<rss" in feed and "Attention surge" in feed
    assert (config.PUBLIC_DIR / "leaderboard.html").exists()
    assert site["leaderboard"] == 50


def test_x_adapter(env, monkeypatch):
    from ioi import config, fetch
    from ioi.sources import x

    monkeypatch.setattr(config, "X_BEARER_TOKEN", "test")
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url)
        if request.url.path.endswith("/users/by"):
            names = request.url.params["usernames"].split(",")
            return httpx.Response(200, json={"data": [
                {"id": str(1000 + i), "username": n, "public_metrics": {"followers_count": 10}} for i, n in enumerate(names)]})
        assert request.url.params["exclude"] == "replies,retweets"
        return httpx.Response(200, json={"data": [
            {"id": "555", "text": "hello ETF", "created_at": "2026-11-10T10:00:00.000Z",
             "public_metrics": {"like_count": 3, "retweet_count": 1, "reply_count": 0, "quote_count": 0}}]})

    monkeypatch.setattr(x, "_client", lambda: httpx.Client(base_url="https://api.x.com/2", transport=httpx.MockTransport(handler)))
    rep = fetch.verify()
    assert len([k for k in rep["ok"]]) >= 34  # all X accounts resolved in one batched call
    assert sum(1 for u in calls if u.path.endswith("/users/by")) == 1
    s = fetch.fetch_all("x")
    assert s["accounts"] == 34 and s["new_posts"] == 1  # same fake post id for everyone -> stored once
    acc = env.rows("SELECT last_seen_id FROM accounts WHERE key = 'vitalik'")[0]
    assert acc["last_seen_id"] == "555"
    first_call = [u for u in calls if "/tweets" in u.path][0]
    assert first_call.params["max_results"] == "5"


def test_bluesky_adapter(monkeypatch):
    from ioi.sources import bluesky

    feed = {"feed": [
        {"post": {"uri": "at://did:plc:x/app.bsky.feed.post/abc", "record": {"text": "BTC up", "createdAt": "2026-11-10T09:00:00.000Z"}, "likeCount": 2}},
        {"post": {"uri": "at://did:plc:x/app.bsky.feed.post/def", "record": {"text": "rt", "createdAt": "2026-11-10T08:00:00Z"}}, "reason": {"$type": "repost"}},
    ]}
    monkeypatch.setattr(bluesky.httpx, "get", lambda *a, **k: httpx.Response(200, json=feed, request=httpx.Request("GET", "https://x")))
    posts = bluesky.fetch({"key": "k", "handle": "someone.bsky.social"})
    assert len(posts) == 1 and posts[0]["url"].endswith("/post/abc")


def test_telegram_formatting():
    from ioi.notify import _chunks, md_to_telegram

    out = md_to_telegram("# Title\n- **BTC** <up> [src](https://x.com/a)\n*Not advice*")
    assert "<b>Title</b>" in out and "&lt;up&gt;" in out and '<a href="https://x.com/a">src</a>' in out
    assert "<i>Not advice</i>" in out
    assert all(len(c) <= 3900 for c in _chunks("line\n" * 3000))


def test_weekly_retention_budget(env, monkeypatch):
    from ioi import config, fetch, label, llm, weekly
    from ioi.sources import x

    db = env
    t0 = db.now() - timedelta(days=2)
    # 4 posts per account for 3 accounts; one breakout post per account
    for k in ("wu", "coindesk", "saylor"):
        for i in range(4):
            db.upsert_post({"id": f"x:{k}{i}", "account_key": k, "platform": "x", "url": f"https://x.com/{k}/{i}",
                            "text": f"Big ETF chart number {i} from {k}", "created_at": db.iso(t0 + timedelta(hours=i)),
                            "likes": 1000 if i == 3 else 10})
    monkeypatch.setattr(llm, "complete", lambda s, u, m, max_tokens=0, provider=None:
                        fake_complete(s, u, m) if "You label" in s else "# What worked\n\nCharts won.")
    label.label_pending()
    data = weekly.outliers()
    assert data["top"][0]["lift"] > 10 and data["posts_analysed"] == 12
    r = weekly.build()
    assert r["title"] == "What worked"

    # retention: an old post is removed together with its label
    db.upsert_post({"id": "x:old", "account_key": "wu", "platform": "x", "url": "u", "text": "old ETF news here",
                    "created_at": db.ago(days=200)})
    label.label_pending()
    assert db.purge_old(90) == 1
    assert not db.rows("SELECT 1 AS x FROM labels WHERE post_id = 'x:old'")

    # budget guard: once today's X reads hit the cap, X accounts are skipped
    monkeypatch.setattr(config, "X_BEARER_TOKEN", "t")
    monkeypatch.setattr(config, "X_MAX_READS_PER_DAY", 0)
    called = []
    monkeypatch.setattr(x, "fetch", lambda a: called.append(a) or [])
    s = fetch.fetch_all("x")
    assert s["x_budget_hit"] and not called


def test_niche_profile_prompt():
    from ioi import config, label

    assert "crypto" in config.AUDIENCE and "BTC" in label.system_prompt()


def test_removed_accounts_are_deactivated(env, tmp_path, monkeypatch):
    from ioi import config

    db = env
    csv_path = tmp_path / "a.csv"
    csv_path.write_text("key,name,category,platform,handle,active\nwu,Wu,news,x,WuBlockchain,1\n")
    monkeypatch.setattr(config, "ACCOUNTS_CSV", csv_path)
    db.sync_accounts()
    assert db.rows("SELECT COUNT(*) AS n FROM accounts WHERE active = 1")[0]["n"] == 1
