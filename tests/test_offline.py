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
    assert "<rss" in feed and "Attention surge" not in feed  # fresh Pro alerts stay out of the public feed
    db.run("UPDATE alerts SET created_at = :t", t=db.ago(hours=4))
    publish.build_site()
    assert "Attention surge" in (config.PUBLIC_DIR / "feed.xml").read_text()
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


def test_parse_json_salvages_truncated_reply():
    from ioi.llm import parse_json

    reply = '```json\n[\n{"i": 0, "topics": ["BTC"]},\n{"i": 1, "topics": ["ETH"]},\n{"i": 2, "top'
    out = parse_json(reply)
    assert [o["i"] for o in out] == [0, 1]


def test_landing_teasers_and_social(env, monkeypatch):
    from ioi import cards, config, digest, influence, label, llm, notify, publish, social

    monkeypatch.setattr(cards, "_chrome", lambda: None)  # no images in this test

    db = env
    seed_posts(db)
    monkeypatch.setattr(llm, "complete", fake_complete)
    label.label_pending()
    digest.build()

    # landing page: live data, no dead buttons without links
    publish.build_site()
    page = (config.PUBLIC_DIR / "index.html").read_text()
    assert "Eric Balchunas" in page and "Pro opens soon" in page and "$" not in page.replace("$BTC", "")
    assert (config.PUBLIC_DIR / "daily" / "index.html").exists()
    monkeypatch.setattr(config, "PRO_CHECKOUT_URL", "https://whop.com/ioi/pro")
    publish.build_site()
    assert 'href="https://whop.com/ioi/pro"' in (config.PUBLIC_DIR / "index.html").read_text()

    # pro teasers: only after the delay, once per alert
    sent = []
    monkeypatch.setattr(config, "TELEGRAM_PUBLIC_CHAT_ID", "-100")
    monkeypatch.setattr(notify, "send", lambda chat, text, is_html=True: sent.append((chat, text)) or True)
    influence.new_alerts()
    assert notify.pro_teasers() == 0  # too fresh
    db.run("UPDATE alerts SET created_at = :t", t=db.ago(hours=4))
    assert notify.pro_teasers() == 2 and "whop.com/ioi/pro" in sent[-1][1]
    assert notify.pro_teasers() == 0

    # social: X gets no link + OAuth header, Bluesky gets a clickable link, no topic twice
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "📊 5 of our 50 accounts are on the BTC ETF. Eric Balchunas was first. https://spam.example @someone")
    monkeypatch.setattr(config, "X_API_KEY", "k"); monkeypatch.setattr(config, "X_API_SECRET", "s")
    monkeypatch.setattr(config, "X_ACCESS_TOKEN", "t"); monkeypatch.setattr(config, "X_ACCESS_SECRET", "ts")
    monkeypatch.setattr(config, "BLUESKY_HANDLE", "ioi.bsky.social"); monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "p")
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append((url, json, headers))
        req = httpx.Request("POST", url)
        if url.endswith("/tweets"):
            assert headers["Authorization"].startswith("OAuth ") and "oauth_signature" in headers["Authorization"]
            assert "http" not in json["text"] and "@" not in json["text"]
            return httpx.Response(201, json={"data": {"id": "9"}}, request=req)
        if url.endswith("createSession"):
            return httpx.Response(200, json={"accessJwt": "j", "did": "did:plc:me"}, request=req)
        assert json["record"]["facets"][0]["features"][0]["uri"].startswith(config.SITE_URL)
        return httpx.Response(200, json={"uri": "at://did:plc:me/app.bsky.feed.post/xyz"}, request=req)

    monkeypatch.setattr(social.httpx, "post", fake_post)
    r = social.run_once()
    assert r["results"]["x"].endswith("/9") and r["results"]["bluesky"].endswith("/xyz") and r["results"]["telegram"] == "ok"
    r2 = social.run_once()
    assert r2.get("topic") != r["topic"]  # never the same topic twice in 24h

    monkeypatch.setattr(llm, "complete", lambda *a, **k: "Buy now before it pumps")
    db.run("DELETE FROM social_posts")
    with pytest.raises(ValueError):
        social.run_once()


def test_pro_access_flow(env, monkeypatch):
    from ioi import config, pro

    db = env
    for k, v in {"WHOP_API_KEY": "k", "WHOP_COMPANY_ID": "biz_1", "WHOP_PRODUCT_ID": "prod_1",
                 "TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_PRO_CHAT_ID": "-100pro"}.items():
        monkeypatch.setattr(config, k, v)
    monkeypatch.setattr(pro.notify, "status", lambda *a, **k: True)
    whop = {"mem_a": "trialing", "mem_other": "active"}
    updates = [
        {"update_id": 1, "message": {"chat": {"type": "private"}, "from": {"id": 11}, "text": "hi"}},
        {"update_id": 2, "message": {"chat": {"type": "private"}, "from": {"id": 11}, "text": "My mail: Ann@Example.com"}},
        {"update_id": 3, "message": {"chat": {"type": "private"}, "from": {"id": 22}, "text": "ann@example.com"}},
        {"update_id": 4, "message": {"chat": {"type": "private"}, "from": {"id": 33}, "text": "nobody@x.io"}},
    ]
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        assert headers["Authorization"] == "Bearer k" and params["company_id"] == "biz_1"
        data = [{"id": mid, "status": st, "product": {"id": "prod_1" if mid == "mem_a" else "prod_2"},
                 "user": {"id": "u", "email": "ann@example.com"}} for mid, st in whop.items()]
        return httpx.Response(200, json={"data": data, "page_info": {"has_next_page": False}}, request=httpx.Request("GET", url))

    def fake_post(url, json=None, timeout=None):
        method = url.rsplit("/", 1)[-1]
        calls.append((method, json))
        res = {"getUpdates": [u for u in updates if u["update_id"] >= (json or {}).get("offset", 0)],
               "createChatInviteLink": {"invite_link": "https://t.me/+abc"}}.get(method, True)
        return httpx.Response(200, json={"ok": True, "result": res}, request=httpx.Request("POST", url))

    monkeypatch.setattr(pro.httpx, "get", fake_get)
    monkeypatch.setattr(pro.httpx, "post", fake_post)

    assert pro.sync() == {"invited": 1, "removed": 0}
    dms = [(c[1]["chat_id"], c[1]["text"]) for c in calls if c[0] == "sendMessage"]
    assert "Whop" in dms[0][1]                                   # help text
    assert dms[1][0] == 11 and "t.me/+abc" in dms[1][1]          # invited
    assert dms[2][0] == 22 and "already linked" in dms[2][1]     # same membership, other account
    assert dms[3][0] == 33 and "couldn't find" in dms[3][1]
    link = [c[1] for c in calls if c[0] == "createChatInviteLink"][0]
    assert link["member_limit"] == 1 and link["chat_id"] == "-100pro"
    assert pro._kv("tg_offset") == "5"

    whop["mem_a"] = "canceled"
    calls.clear()
    assert pro.sync()["removed"] == 1
    assert [c[0] for c in calls][:3] == ["getUpdates", "banChatMember", "unbanChatMember"]
    calls.clear()
    assert pro.sync()["removed"] == 0  # removed only once


def test_weekly_teaser_hides_pro_part():
    from ioi.publish import teaser

    md = "# T\nintro\n## What worked\nA\n## Copy this next week\nSECRET\n## Best time\nX"
    t = teaser(md)
    assert "What worked" in t and "SECRET" not in t


def test_llm_fallback_chain(monkeypatch):
    from ioi import config, llm

    monkeypatch.setattr(config, "OPENAI_API_KEY", "or-key")
    monkeypatch.setattr(config, "LLM_FALLBACK_MODELS", ["oss/a", "oss/b"])
    monkeypatch.setattr(llm, "_notify", lambda t: None)
    calls = []

    def fake_one(system, user, model, max_tokens, provider):
        calls.append((provider, model))
        if model in ("claude-x", "oss/a"):
            raise RuntimeError("down")
        return "ok from " + model

    monkeypatch.setattr(llm, "_one", fake_one)
    assert llm.complete("s", "u", "claude-x", provider="anthropic") == "ok from oss/b"
    assert calls == [("anthropic", "claude-x"), ("openai", "oss/a"), ("openai", "oss/b")]
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    with pytest.raises(llm.LLMError):
        llm.complete("s", "u", "claude-x", provider="anthropic")


def test_cards_upload_top50_and_scout(env, monkeypatch, tmp_path):
    from ioi import cards, config, label, llm, notify, scout, social

    db = env
    seed_posts(db)
    monkeypatch.setattr(llm, "complete", fake_complete)
    label.label_pending()
    img = tmp_path / "card.png"
    img.write_bytes(b"\x89PNG" + b"0" * 6000)
    monkeypatch.setattr(cards, "render", lambda *a, **k: img)
    for k, v in {"X_API_KEY": "k", "X_API_SECRET": "s", "X_ACCESS_TOKEN": "t", "X_ACCESS_SECRET": "ts",
                 "BLUESKY_HANDLE": "ioi.bsky.social", "BLUESKY_APP_PASSWORD": "p", "TELEGRAM_BOT_TOKEN": "b",
                 "TELEGRAM_PUBLIC_CHAT_ID": "-1", "TELEGRAM_STATUS_CHAT_ID": "9"}.items():
        monkeypatch.setattr(config, k, v)
    seen, status_msgs = [], []
    monkeypatch.setattr(notify, "send", lambda chat, text, is_html=True, buttons=None: status_msgs.append((chat, text, buttons)) or True)

    def fake_post(url, json=None, headers=None, timeout=None, files=None, data=None, content=None):
        seen.append((url, json, bool(files or content)))
        req = httpx.Request("POST", url)
        if url.endswith("/media/upload"):
            return httpx.Response(200, json={"data": {"id": "m1"}}, request=req)
        if url.endswith("/tweets"):
            if "reply" in json:  # approved reply
                assert json["reply"]["in_reply_to_tweet_id"].isdigit() and "@" not in json["text"]
                return httpx.Response(201, json={"data": {"id": "77"}}, request=req)
            assert json["media"]["media_ids"] == ["m1"]
            return httpx.Response(201, json={"data": {"id": "9"}}, request=req)
        if url.endswith("createSession"):
            return httpx.Response(200, json={"accessJwt": "j", "did": "did:plc:me"}, request=req)
        if url.endswith("uploadBlob"):
            return httpx.Response(200, json={"blob": {"ref": "b1"}}, request=req)
        if url.endswith("createRecord"):
            assert json["record"]["embed"]["images"][0]["image"] == {"ref": "b1"}
            return httpx.Response(200, json={"uri": "at://x/app.bsky.feed.post/p1"}, request=req)
        if url.endswith("sendPhoto"):
            return httpx.Response(200, json={"ok": True}, request=req)
        raise AssertionError(url)

    monkeypatch.setattr(httpx, "post", fake_post)
    r = social.weekly_top50()
    assert r["image"] and r["results"]["x"].endswith("/9") and r["results"]["telegram"] == "ok"
    assert "Eric Balchunas" in r["text"] and "@" not in r["text"]  # no automated tags on X
    assert any("@EricBalchunas" in m[1] for m in status_msgs)  # owner gets the tag tip
    assert any(u.endswith("sendPhoto") for u, _, _ in seen)

    monkeypatch.setattr(llm, "complete", lambda *a, **k: '[{"i": 0, "reply": "5 of our 50 accounts are on this, Eric Balchunas was first. https://x.co @a"}]')
    status_msgs.clear()
    out = scout.run_once()
    assert out == {"sent": 1}
    _, msg, buttons = status_msgs[-1]
    assert "x.com/" in msg and "Eric Balchunas" in msg and "https://x.co" not in msg.split("<code>")[1]
    assert buttons[0][0]["url"].startswith("https://x.com/intent/post?in_reply_to=")
    tid = buttons[0][0]["url"].split("in_reply_to=")[1].split("&")[0]
    assert tid.isdigit() and "text=" in buttons[0][0]["url"]

    # old callback buttons are acknowledged (never raise on a late answer) and post nothing
    from ioi import pro

    def late(method, **p):
        if method == "answerCallbackQuery":
            raise RuntimeError("query is too old")
        return True

    monkeypatch.setattr(pro, "tg", late)
    cq = {"id": "c1", "data": f"rp:{tid}", "message": {"message_id": 5, "chat": {"id": 9}}}
    assert scout.handle_button(cq) == "legacy"
    assert scout.handle_button({**cq, "message": {"message_id": 5, "chat": {"id": 1234}}}) == "ignored"
    assert not any(u.endswith("/tweets") and "reply" in (j or {}) for u, j, _ in seen)


def test_newswire(env, monkeypatch):
    from ioi import config, label, llm, news, notify

    db = env
    seed_posts(db)
    monkeypatch.setattr(llm, "complete", fake_complete)
    label.label_pending()
    now = db.now()
    rss = f"""<?xml version="1.0"?><rss><channel><item><title>SEC approves spot ETF for bitcoin</title>
<link>https://www.sec.gov/news/1</link><pubDate>{now.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate></item>
<item><title>Old story</title><link>https://x/old</link><pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate></item></channel></rss>"""

    def fake_get(url, params=None, headers=None, timeout=None, follow_redirects=None):
        req = httpx.Request("GET", url)
        if "binance" in url:
            return httpx.Response(200, json={"data": {"catalogs": [{"articles": [
                {"code": "abc", "title": "Binance Will List Foo (FOO)", "releaseDate": int(now.timestamp() * 1000)}]}]}}, request=req)
        if "upbit" in url:
            return httpx.Response(500, request=req)  # one source down: others continue
        return httpx.Response(200, content=rss.encode(), request=req)

    monkeypatch.setattr(news.httpx, "get", fake_get)
    monkeypatch.setattr(news, "_social", lambda src: None)  # no X/Telegram in this test
    monkeypatch.setattr(news, "_cryptopanic", lambda src: None)
    news.FETCH.update({"x": news._social, "telegram": news._social, "cryptopanic": news._cryptopanic})
    f = news.fetch_all()
    assert f["new"] >= 2 and not any("upbit" in e for e in f["errors"])
    assert not db.rows("SELECT 1 AS x FROM news WHERE title = 'Old story'")  # no backlog
    titles = [r["title"] for r in db.rows("SELECT title FROM news")]
    assert "Binance Will List Foo (FOO)" in titles

    def fake_label(system, user, model, max_tokens=2000, provider=None):
        lines = [ln for ln in user.splitlines()[1:] if ln.strip()]
        return json.dumps([{"i": i, "topics": ["ETF" if "ETF" in ln else "FOO"], "impact": 9,
                            "teaser": "Washington just blinked: the SEC gave the green light. https://spam"} for i, ln in enumerate(lines)])

    monkeypatch.setattr(llm, "complete", fake_label)
    assert news.label_pending()["labeled"] >= 2
    sent = []
    monkeypatch.setattr(config, "TELEGRAM_PRO_CHAT_ID", "-100pro")
    monkeypatch.setattr(notify, "send", lambda chat, text, is_html=True, buttons=None: sent.append((chat, text)) or True)
    alerts = news.newswire_alerts()
    assert alerts and all(c == "-100pro" for c, _ in sent)
    assert any("NEWSWIRE" in t and "https://spam" not in t for _, t in sent)
    assert news.newswire_alerts() == []  # once only
    d = news.for_digest()
    etf = [x for x in d if x["topic"] == "ETF"][0]
    assert etf["voices"] == 5 and etf["voices_minutes_ahead"] > 0  # the voices were ahead of the SEC


def test_news_label_accepts_wrapped_json(env, monkeypatch):
    from ioi import llm, news

    db = env
    db.run("""INSERT INTO news (id, source, name, kind, title, url, published_at, fetched_at, labeled)
              VALUES ('a', 's', 'S', 'media', 'Big headline', 'u', :t, :t, 0)""", t=db.iso(db.now()))
    monkeypatch.setattr(llm, "complete", lambda *a, **k: '{"items": [{"i": 0, "topics": ["btc"], "impact": 7, "story": "Big Story!", "teaser": "Hook. Fact."}]}')
    assert news.label_pending()["labeled"] == 1
    r = db.rows("SELECT topics, impact, teaser FROM news WHERE id = 'a'")[0]
    assert r["topics"] == '["BTC"]' and r["impact"] == 7 and r["teaser"] == "Hook. Fact."
    assert db.rows("SELECT story FROM news WHERE id = 'a'")[0]["story"] == "bigstory"


def test_reset_engine_disposes(env):
    from ioi import db

    e = db.engine()
    disposed = []
    orig = e.dispose
    e.dispose = lambda *a, **k: (disposed.append(1), orig(*a, **k))
    db.reset_engine()
    assert disposed and db._engine is None
