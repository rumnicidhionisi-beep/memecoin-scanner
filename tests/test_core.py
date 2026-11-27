import time

import pytest

from scanner import agents, alerts, explain, narratives as nar, scoring
from scanner.config import load_config
from scanner.main import Listener
from scanner.sources import dexscreener as dex, news, x_twitter
from scanner.store import Store
from scanner.telegram import Telegram

NOW_MS = time.time() * 1000
MINT = "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr"


def pair(sym, name, mint, age_h=5, liq=40000, mc=300000, v1=60000, v24=400000, b1=400, s1=250,
         chg1=12, chg5=3, b5=40, s5=30, socials=None):
    return {
        "chainId": "solana", "dexId": "pumpswap", "url": f"https://dexscreener.com/solana/{mint}",
        "baseToken": {"address": mint, "symbol": sym, "name": name},
        "priceUsd": "0.0003", "marketCap": mc, "fdv": mc, "liquidity": {"usd": liq},
        "pairCreatedAt": NOW_MS - age_h * 3_600_000,
        "volume": {"m5": 5000, "h1": v1, "h6": v1 * 5, "h24": v24},
        "txns": {"m5": {"buys": b5, "sells": s5}, "h1": {"buys": b1, "sells": s1},
                 "h24": {"buys": b1 * 10, "sells": s1 * 10}},
        "priceChange": {"m5": chg5, "h1": chg1, "h24": 200},
        "info": {"socials": socials or [{"type": "twitter", "url": "https://x.com/asteroidcoin"}]},
    }


def tweet(id_, text, author="elonmusk", tier=1, kind="post", context="", context_author="", mints=None):
    return {"source": "x", "id": f"x:{id_}", "text": text, "url": f"https://x.com/{author}/status/{id_}",
            "author": author, "followers": 2e8, "likes": 300000, "retweets": 50000, "views": 5e7,
            "ts": time.time(), "tier": tier, "kind": kind, "context": context,
            "context_author": context_author, "mints": mints or []}


@pytest.fixture
def cfg():
    c = load_config()
    c["secrets"].update(twitterapi_key="", apify_token="", anthropic_key="")
    return c


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "state.json")


@pytest.fixture
def tg():
    return Telegram("", "", dry_run=True)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(agents.rugcheck, "summary", lambda m: {"score": 12, "danger": [], "risks": []})
    monkeypatch.setattr(agents.x_twitter, "mentions", lambda *a, **k: [])
    monkeypatch.setattr(agents.dex, "search", lambda q: [])
    monkeypatch.setattr(agents.dex, "pairs_for_tokens", lambda mints: [])
    monkeypatch.setattr(agents.dex, "latest_solana_tokens", lambda: [])


# ------------------------------------------------------------- units
def test_heuristic_keywords():
    k = nar.heuristic_keywords('Make Asteroid the official mascot of SpaceX! $ASTEROID #kekius "doge father"')
    assert "ASTEROID" in k and "kekius" in k and "doge father" in k and "the" not in k


def test_keyword_match():
    assert nar.keyword_matches_token("$ASTEROID", "ASTEROID", "Asteroid") == 1.0
    assert nar.keyword_matches_token("asteroid", "ASTR", "Asteroid Shiba") == 0.7
    assert nar.keyword_matches_token("cat", "DOG", "Dog") == 0.0


def test_find_mints():
    assert x_twitter.find_mints(f"ape this {MINT} now") == [MINT]
    assert x_twitter.find_mints("CA: 9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump") == [
        "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump"]
    assert x_twitter.find_mints("just a normal tweet") == []


def test_reply_normalisation():
    t = {"id": "5", "text": "Will answer shortly", "isReply": True, "inReplyToId": "4",
         "inReplyToUsername": "glennbeck", "author": {"userName": "elonmusk", "followers": 1}}
    p = x_twitter._norm(t, 1)
    assert p["kind"] == "reply" and p["reply_to_id"] == "4" and p["context_author"] == "glennbeck"
    rt = {"id": "6", "text": "RT", "isRetweet": True, "author": {"userName": "elonmusk"},
          "retweeted_tweet": {"text": "Meet Broccoli my dog", "author": {"userName": "cz_binance"}}}
    p = x_twitter._norm(rt, 1)
    assert p["kind"] == "retweet" and "Broccoli" in p["context"]


def test_real_age_uses_oldest_pair():
    a = pair("X", "X", "M1", age_h=1, liq=50000)
    b = pair("X", "X", "M1", age_h=30, liq=1000)
    best = dex.best_pair_per_token([a, b])["M1"]
    assert best["liquidity"]["usd"] == 50000
    assert dex.metrics(best)["age_h"] == pytest.approx(30, rel=0.01)


def test_early_lane_catches_fast_coin(cfg):
    oc = cfg["onchain"]
    fast = dex.metrics(pair("FAST", "Fast", "M", age_h=0.4, mc=60000, v1=30000, b1=500, s1=300))
    assert scoring.onchain_lane(fast, oc) == "early"
    slow = dex.metrics(pair("S", "S", "M", age_h=0.4, mc=20000))
    assert scoring.onchain_lane(slow, oc) is None
    old = dex.metrics(pair("O", "O", "M", age_h=10))
    assert scoring.onchain_lane(old, oc) == "survivors"
    bots = dex.metrics(pair("B", "B", "M", age_h=0.4, mc=60000, v1=30000, b1=2000, s1=1500))
    assert scoring.onchain_lane(bots, oc) is None  # $8 per trade


def test_speed_points(cfg):
    fast = scoring.score(dex.metrics(pair("F", "F", "M", age_h=0.3, mc=70000)), {"score": 10, "danger": []}, cfg)
    late = scoring.score(dex.metrics(pair("L", "L", "M", age_h=20, mc=70000)), {"score": 10, "danger": []}, cfg)
    assert fast["parts"]["speed"] == 20 and late["parts"]["speed"] == 0
    assert any("λεπτά" in r for r in fast["reasons"])


def test_score_rejects_mint_authority(cfg):
    res = scoring.score(dex.metrics(pair("A", "A", "M")), {"score": 10, "mint_authority": True, "danger": []}, cfg)
    assert res["rejected"]


def test_score_trap_pattern(cfg):
    good = scoring.score(dex.metrics(pair("A", "A", "M")), {"score": 10, "danger": []}, cfg)
    trap = scoring.score(dex.metrics(pair("A", "A", "M", b1=900, s1=100, chg1=-30)), {"score": 10, "danger": []}, cfg)
    assert trap["score"] < good["score"] and any("παγίδα" in f for f in trap["flags"])


def test_rss_parse():
    xml = """<rss><channel><item><title>Elon Musk replies to Asteroid mascot wish</title>
    <link>https://ex.com/a</link><description>&lt;b&gt;hi&lt;/b&gt;</description>
    <pubDate>Thu, 26 Nov 2026 10:00:00 GMT</pubDate></item></channel></rss>"""
    it = news.parse_rss(xml)[0]
    assert it["title"].startswith("Elon") and it["desc"] == "hi"


def test_fallback_explanation_for_reply(cfg):
    m = dex.metrics(pair("ASTEROID", "Asteroid", "A", age_h=0.3))
    n = {"keywords": ["asteroid"], "author": "elonmusk", "source": "x", "kind": "reply", "tier": 1,
         "context_author": "glennbeck", "text": "Will answer shortly"}
    ex = explain.fallback(m, scoring.score(m, None, cfg, n, 1.0), n, [], 4)
    assert "απάντησε" in ex["what"] and "glennbeck" in ex["what"] and ex["potential"] == "Υψηλό"
    assert "4 αντίγραφα" in ex["potential_why"]


# ------------------------------------------------------------- end to end
def test_musk_reply_to_coin_alert(monkeypatch, cfg, store, tg):
    """Musk replies 'Will answer shortly' to a post about Asteroid -> $ASTEROID found -> explained alert."""
    t = tweet(1, "Will answer shortly", kind="reply", context_author="glennbeck",
              context="Her final wish: make Asteroid the official mascot of SpaceX")
    monkeypatch.setattr(agents, "collect_posts", lambda c, s, src=None: [t])
    monkeypatch.setattr(agents.dex, "search", lambda q: [
        pair("ASTEROID", "Asteroid", "AST1", age_h=0.4, mc=90000),
        pair("ASTEROID", "Asteroid", "AST2", age_h=0.3, mc=12000, v1=2500),
        pair("ASTRO", "Astro", "AST3", v1=100)] if "steroid" in q.lower() else [])
    stats = {}
    agents.narrative_agent(cfg, store, tg, stats)
    assert stats["narratives"] == 1
    msg = tg.sent[0]
    assert "$ASTEROID" in msg and "Γιατί βγήκε" in msg and "Περιθώριο" in msg and "glennbeck" in msg
    assert "πάνω σε αφήγημα" in msg
    n_before = len(tg.sent)
    monkeypatch.setattr(agents, "collect_posts", lambda c, s, src=None: [])
    agents.narrative_agent(cfg, store, tg, {})  # no duplicate
    assert len(tg.sent) == n_before


def test_fresh_narrative_rechecked_until_coin_appears(monkeypatch, cfg, store, tg):
    t = tweet(2, "Kekius Maximus", tier=1)
    monkeypatch.setattr(agents, "collect_posts", lambda c, s, src=None: [t])
    agents.narrative_agent(cfg, store, tg, {})
    assert tg.sent == []  # no coin yet (and no Claude -> no narrative-only message)
    # 5 minutes later the coin appears on DexScreener
    monkeypatch.setattr(agents, "collect_posts", lambda c, s, src=None: [])
    monkeypatch.setattr(agents.dex, "search", lambda q: [pair("KEKIUS", "Kekius Maximus", "K1", age_h=0.1, mc=30000)]
                        if "kekius" in q.lower() else [])
    agents.narrative_agent(cfg, store, tg, {})
    assert len(tg.sent) == 1 and "$KEKIUS" in tg.sent[0]


def test_contract_in_kol_tweet(monkeypatch, cfg, store, tg):
    t = tweet(3, f"this one is cooking {MINT}", author="blknoiz06", tier=2, mints=[MINT])
    monkeypatch.setattr(agents, "collect_posts", lambda c, s, src=None: [t])
    monkeypatch.setattr(agents.dex, "pairs_for_tokens",
                        lambda mints: [pair("ZZZ", "Something", MINT, age_h=0.5, mc=40000)] if MINT in mints else [])
    agents.narrative_agent(cfg, store, tg, {})
    assert len(tg.sent) == 1 and "$ZZZ" in tg.sent[0] and "@blknoiz06" in tg.sent[0]


def test_narrative_only_alert_needs_claude(monkeypatch, cfg, store, tg):
    t = tweet(4, "Broccoli is my dog's name", author="cz_binance")
    monkeypatch.setattr(agents, "collect_posts", lambda c, s, src=None: [t])
    agents.narrative_agent(cfg, store, tg, {})
    assert tg.sent == []
    cfg["secrets"]["anthropic_key"] = "k"
    t2 = tweet(5, "Broccoli is my dog's name", author="cz_binance")
    monkeypatch.setattr(agents, "collect_posts", lambda c, s, src=None: [t2])
    monkeypatch.setattr(agents.nar, "_claude", lambda posts, k, m: [
        {"i": 0, "meme": True, "strength": 9, "keywords": ["broccoli"], "why": "Το σκυλί του CZ"}])
    monkeypatch.setattr(agents.explain, "post_json", lambda *a, **k: None)
    agents.narrative_agent(cfg, store, tg, {})
    assert len(tg.sent) == 1 and "Νέο αφήγημα" in tg.sent[0] and "broccoli" in tg.sent[0]


def test_onchain_early_coin_with_mentions(monkeypatch, cfg, store, tg):
    cfg["secrets"]["twitterapi_key"] = "k"
    monkeypatch.setattr(agents.x_twitter, "account_age_days", lambda *a: 400)
    monkeypatch.setattr(agents.dex, "latest_solana_tokens", lambda: ["E1"])
    monkeypatch.setattr(agents.dex, "pairs_for_tokens",
                        lambda mints: [pair("RUNNER", "Runner", "E1", age_h=0.4, mc=65000, v1=40000)])
    monkeypatch.setattr(agents.x_twitter, "mentions", lambda *a, **k: [
        {"author": "kolguy", "followers": 120000, "likes": 900, "text": "$RUNNER the dog from the viral video"},
        {"author": "kol2", "followers": 8000, "likes": 50, "text": "$RUNNER"}])
    agents.onchain_agent(cfg, store, tg, {})
    assert len(tg.sent) == 1
    assert "γρήγορο νόμισμα" in tg.sent[0] and "@kolguy" in tg.sent[0]


def test_pumpfun_watchlist(monkeypatch, cfg, store, tg):
    store.add_narrative({"keywords": ["kekius"], "source": "x", "author": "elonmusk", "text": "Kekius",
                         "url": "", "weight": 1.5, "tier": 1, "pid": "x:9", "mints": []})
    li = Listener(cfg, store, tg)
    for i in range(4):
        li.on_new_token({"mint": f"K{i}", "symbol": "KEKIUS", "name": "Kekius Maximus", "txType": "create"})
    li.on_new_token({"mint": "Z", "symbol": "ZZZ", "name": "nothing"})
    assert len(tg.sent) == 2  # ⚡ early warning capped
    li.drain()
    assert set(store.watched(6)) == {"K0", "K1", "K2", "K3"}
    # later K2 gets traction -> full alert
    monkeypatch.setattr(agents.dex, "pairs_for_tokens",
                        lambda mints: [pair("KEKIUS", "Kekius Maximus", "K2", age_h=0.3, mc=55000)])
    agents.onchain_agent(cfg, store, tg, {})
    assert "νέο pump.fun" in tg.sent[-1] and "$KEKIUS" in tg.sent[-1]


def test_digest(monkeypatch, cfg, store, tg):
    assert not store.digest_due(30)  # first run starts the clock
    store.add_event({"type": "coin", "symbol": "AST", "mint": "M", "mc": 90000, "score": 80, "why": "Musk"})
    store.add_event({"type": "news", "text": "Musk replies", "url": "https://x"})
    store.set_cursor("digest_last", time.time() - 31 * 60)
    agents.maybe_digest(cfg, store, tg)
    assert "Σύνοψη" in tg.sent[0] and "$AST" in tg.sent[0] and "Musk replies" in tg.sent[0]
    agents.maybe_digest(cfg, store, tg)
    assert len(tg.sent) == 1


def test_store_roundtrip(store):
    store.mark_seen("a")
    store.add_narrative({"keywords": ["x"], "weight": 1, "pid": "p"})
    store.watch("M", "p")
    store.save()
    s2 = Store(store.path)
    assert s2.seen("a") and s2.active_narratives(24) and "M" in s2.watched(6)


def test_alert_html_escaping(cfg):
    m = dex.metrics(pair("<B>", "a&b", "M"))
    res = scoring.score(m, None, cfg)
    txt = alerts.token_alert(m, res, cfg, None, explain.fallback(m, res, None, [], 0))
    assert "&lt;B&gt;" in txt and "a&amp;b" in txt


def test_rugcheck_report_parsing():
    from scanner.sources import rugcheck
    data = {"score_normalised": 15, "mintAuthority": None, "freezeAuthority": None,
            "creator": "DEV", "creatorBalance": 30_000_000, "token": {"supply": 1_000_000_000_000_000, "decimals": 6},
            "markets": [{"pubkey": "POOL" * 9, "liquidityA": "POOLVAULT" * 4, "lp": {"holders": []}}],
            "topHolders": [{"address": "POOLVAULT" * 4, "owner": "POOL" * 9, "pct": 20.0, "insider": False},
                           {"address": "A1", "owner": "O1", "pct": 4.0, "insider": True},
                           {"address": "A2", "owner": "O2", "pct": 3.0, "insider": False}],
            "risks": []}
    r = rugcheck._from_report(data)
    assert r["top10_pct"] == 7.0            # pool excluded
    assert r["dev_pct"] == pytest.approx(3.0)
    assert r["insiders_pct"] == 4.0 and r["insiders_count"] == 1
    assert not r["mint_authority"] and r["full"]


def test_holder_limits(cfg):
    m = dex.metrics(pair("A", "A", "M"))
    clean = scoring.score(m, {"score": 10, "danger": [], "top10_pct": 15, "dev_pct": 1, "insiders_pct": 5,
                              "insiders_count": 2, "full": True}, cfg)
    dirty = scoring.score(m, {"score": 10, "danger": [], "top10_pct": 45, "dev_pct": 12, "insiders_pct": 30,
                              "insiders_count": 9, "full": True}, cfg)
    assert clean["parts"]["safety"] > dirty["parts"]["safety"]
    assert any("dev" in f for f in dirty["flags"]) and any("Insiders" in f for f in dirty["flags"])
    rej = scoring.score(m, {"score": 10, "danger": [], "insiders_pct": 55, "full": True}, cfg)
    assert rej["rejected"]
    txt = alerts.token_alert(m, clean, cfg, None, explain.fallback(m, clean, None, [], 0))
    assert "Top10 15.0%" in txt and "Dev 1.0%" in txt


def test_parse_claude_output():
    assert nar.parse_json_list('```json\n[{"i":0,"meme":true}]\n```') == [{"i": 0, "meme": True}]
    # truncated output -> salvage complete objects
    out = nar.parse_json_list('[{"i":0,"meme":true,"keywords":["a"]},{"i":1,"meme":fal')
    assert out == [{"i": 0, "meme": True, "keywords": ["a"]}]
    assert nar.parse_json_list("sorry") is None


def test_truth_social_feed(monkeypatch, cfg):
    xml = """<rss><channel><item><title>Long post</title><link>https://www.trumpstruth.org/statuses/1</link>
    <description>&lt;p&gt;My dog Buddy is the best dog in America!&lt;/p&gt;</description>
    <pubDate>%s</pubDate></item></channel></rss>""" % time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime())
    monkeypatch.setattr(news, "get_text", lambda url: xml if "trumpstruth" in url else None)
    posts = news.fetch(cfg)
    assert len(posts) == 1 and posts[0]["source"] == "truth" and posts[0]["tier"] == 1
    assert "Buddy" in posts[0]["text"]


def test_profile_change_and_new_follow(monkeypatch, cfg, store, tg):
    from scanner.sources import x_watch
    cfg["secrets"]["twitterapi_key"] = "k"
    cfg["x"]["watch"] = {"enabled": True, "profiles": ["elonmusk"], "follows": ["elonmusk"],
                         "profiles_every_minutes": 0, "follows_every_minutes": 0}
    prof = {"name": "Elon Musk", "description": "", "picture": "a"}
    fol = [{"handle": "old1", "name": "x", "bio": "", "followers": 10, "created": ""}]
    monkeypatch.setattr(agents.x_watch, "profile", lambda k, h: dict(prof))
    monkeypatch.setattr(agents.x_watch, "recent_followings", lambda k, h, n=20: list(fol))
    agents.social_watch(cfg, store, tg, {})          # baseline
    assert tg.sent == []
    prof["name"] = "Kekius Maximus"
    fol.insert(0, {"handle": "frogcoin", "name": "Frog", "bio": "$FROG on sol", "followers": 900, "created": ""})
    stats = {}
    agents.social_watch(cfg, store, tg, stats)
    assert stats == {"profile_changes": 1, "new_follows": 1}
    assert any("Kekius Maximus" in m for m in tg.sent) and any("frogcoin" in m for m in tg.sent)
    kws = [k.lower() for n in store.active_narratives(24) for k in n["keywords"]]
    assert "kekius" in kws and "frogcoin" in kws
    assert x_watch.looks_crypto({"name": "Frog", "bio": "$FROG on sol"})


def test_social_stats_and_fake_promo(cfg):
    real = [{"author": f"a{i}", "followers": 10000, "likes": 10} for i in range(6)]
    s = agents.social_stats(real, cfg)
    assert s["authors"] == 6 and s["quality_authors"] == 6
    m = dex.metrics(pair("A", "A", "M"))
    good = scoring.score(m, {"score": 10, "danger": []}, cfg, social=s)
    fake = scoring.score(m, {"score": 10, "danger": []}, cfg,
                         social=agents.social_stats([{"author": "bot", "followers": 5, "likes": 0}] * 12, cfg))
    assert good["score"] > fake["score"] and any("τεχνητή" in f for f in fake["flags"])
