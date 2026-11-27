"""The two agents.

Agent 1 — Narrative agent: X (posts, replies, retweets, quotes) + TikTok + news -> narratives ->
          finds the coin on DexScreener as soon as it appears (re-checks fresh narratives every cycle).
Agent 2 — On-chain agent: DexScreener new/boosted coins + pump.fun coins born on a narrative ->
          'early' lane (≥$50K within the first hour) and 'survivors' lane (48h) -> cross-check
          with live narratives -> finds on X WHY the coin exists.
Both share the same safety check, score, Claude explanation and Telegram alert.
"""
import logging
import time

from . import alerts, explain, narratives as nar, scoring
from .sources import dexscreener as dex, news, rugcheck, tiktok, x_twitter

log = logging.getLogger("scanner")


# ---------------------------------------------------------------- shared
def best_narrative_for(m: dict, active: list[dict]) -> tuple[dict | None, float]:
    best, best_match, best_val = None, 0.0, 0.0
    for n in active:
        if m["mint"] in (n.get("mints") or []):
            return n, 1.0
        for k in n["keywords"]:
            s = nar.keyword_matches_token(k, m["symbol"], m["name"])
            if s * n["weight"] > best_val:
                best, best_match, best_val = n, s, s * n["weight"]
    return best, best_match


def evaluate(m: dict, cfg: dict, store, tg, narrative=None, match=0.0, stats=None,
             lane: str = "", copies: int = 0) -> dict | None:
    """Safety check + score + explanation + alert. Returns result or None if rejected."""
    s = cfg["secrets"]
    if not store.should_alert(m["mint"], 100):  # alerted recently: don't spend API calls again
        return None
    rug = rugcheck.summary(m["mint"])
    x_age = None
    if cfg.get("safety", {}).get("check_x_account_age") and s["twitterapi_key"]:
        handle = scoring.x_handle(m.get("twitter", ""))
        if handle:
            x_age = x_twitter.account_age_days(s["twitterapi_key"], handle)
    res = scoring.score(m, rug, cfg, narrative, match, x_age, copies)
    if stats is not None:
        stats["evaluated"] = stats.get("evaluated", 0) + 1
    if res["rejected"]:
        log.info("  ✗ %s rejected: %s", m["symbol"], res["rejected"])
        store.mark_alerted(m["mint"], 0)  # don't re-check a rejected coin for a while
        return None
    log.info("  • %s score %s %s", m["symbol"], res["score"], res["parts"])
    ac = cfg.get("alerts", {})
    if res["score"] < ac.get("min_score", 55) or not store.should_alert(m["mint"], res["score"]):
        return res
    # WHY does this coin exist? -> who talks about it on X
    mentions = [] if narrative else x_twitter.mentions(s["twitterapi_key"], m["symbol"], m["mint"])
    ex = explain.explain(m, res, narrative, mentions, copies,
                         s["anthropic_key"] if ac.get("explain", True) else "", s["anthropic_model"])
    if tg.send(alerts.token_alert(m, res, cfg, narrative, ex, lane)):
        store.mark_alerted(m["mint"], res["score"])
        store.add_event({"type": "coin", "symbol": m["symbol"], "mint": m["mint"], "mc": m["mc"],
                         "score": res["score"], "why": ex["why"]})
        if stats is not None:
            stats["alerts"] = stats.get("alerts", 0) + 1
    return res


# ---------------------------------------------------------------- agent 1
def collect_posts(cfg: dict, store, sources=("x", "tiktok", "news")) -> list[dict]:
    s = cfg["secrets"]
    now = time.time()
    posts = []
    if "x" in sources and cfg.get("x", {}).get("enabled"):
        since = store.cursor("x_since") or now - 15 * 60
        vg = cfg.get("x", {}).get("viral_general") or {}
        viral_due = now - (store.cursor("x_viral_last") or 0) >= vg.get("every_minutes", 30) * 60
        vs = cfg.get("x", {}).get("viral_search") or {}
        meme_due = now - (store.cursor("x_meme_last") or 0) >= vs.get("every_minutes", 10) * 60
        posts += x_twitter.fetch(cfg, s["twitterapi_key"], since - 60, general_viral=viral_due,
                                 memecoin_search=meme_due)
        store.set_cursor("x_since", now)
        if meme_due and s["twitterapi_key"]:
            store.set_cursor("x_meme_last", now)
        if viral_due and s["twitterapi_key"]:
            store.set_cursor("x_viral_last", now)
    tc = cfg.get("tiktok", {})
    if "tiktok" in sources and tc.get("enabled"):
        last = store.cursor("tiktok_last") or 0
        if now - last >= tc.get("every_minutes", 180) * 60:
            posts += tiktok.fetch(cfg, s["apify_token"])
            if s["apify_token"]:
                store.set_cursor("tiktok_last", now)
    if "news" in sources and cfg.get("news", {}).get("enabled"):
        for p in news.fetch(cfg):
            if not store.seen(p["id"]):
                store.add_event({"type": "news", "text": p["text"], "url": p["url"]})
            posts.append(p)
    fresh = [p for p in posts if not store.seen(p["id"])]
    for p in fresh:
        store.mark_seen(p["id"])
    return fresh


def find_tokens_for(n: dict, cfg: dict) -> tuple[list[tuple[dict, float]], int]:
    """Coins matching a narrative. Returns (top 3 [(metrics, match)], number of copies found)."""
    found = {}
    if n.get("mints"):  # contract address written in the post itself
        for mint, p in dex.best_pair_per_token(dex.pairs_for_tokens(n["mints"])).items():
            found[mint] = (dex.metrics(p), 1.0)
    for k in n["keywords"][:3]:
        q = k.lstrip("$#")
        if len(q) < 3:
            continue
        for mint, p in dex.best_pair_per_token(dex.search(q)).items():
            m = dex.metrics(p)
            score = nar.keyword_matches_token(k, m["symbol"], m["name"])
            if score > 0 and (mint not in found or score > found[mint][1]):
                found[mint] = (m, score)
    mc = cfg.get("matching", {})
    young = {a: v for a, v in found.items()
             if v[0]["age_h"] is None or v[0]["age_h"] <= mc.get("max_token_age_hours", 48)}
    ok = [v for v in young.values() if not scoring.narrative_filter(v[0], mc)]
    # leader first: exact match, then volume
    ok.sort(key=lambda t: (t[1], t[0]["vol_1h"]), reverse=True)
    return ok[:3], len(young)


def narrative_agent(cfg: dict, store, tg, stats: dict, sources=("x", "tiktok", "news"),
                    recheck_older: bool = True) -> None:
    log.info("== Agent 1: narratives (%s) ==", ",".join(sources))
    s = cfg["secrets"]
    posts = collect_posts(cfg, store, sources)
    stats["posts"] = stats.get("posts", 0) + len(posts)
    new_narr = nar.extract(posts, s["anthropic_key"], s["anthropic_model"])
    for n in new_narr:
        store.add_narrative(n)
        if n.get("judged") or n.get("tier") == 1 or n.get("mints"):
            store.add_event({"type": "narrative", "author": n.get("author", ""), "keywords": n["keywords"],
                             "why": n.get("why", ""), "weight": n["weight"]})
    stats["narratives"] = stats.get("narratives", 0) + len(new_narr)

    mc = cfg.get("matching", {})
    new_ids = {n["pid"] for n in new_narr}
    now = time.time()
    active = store.active_narratives(mc.get("narrative_ttl_hours", 24))
    # FRESH narratives (< watch_minutes) are re-searched every cycle: the coin often appears minutes later
    fresh = [n for n in active if n.get("pid") not in new_ids
             and now - n["ts"] < mc.get("watch_minutes", 90) * 60]
    fresh.sort(key=lambda n: n["weight"], reverse=True)
    older = []
    if recheck_older:
        older = sorted([n for n in active if n.get("pid") not in new_ids and n not in fresh],
                       key=lambda n: n["weight"], reverse=True)[:5]
    for n in new_narr + fresh[:15] + older:
        tokens, copies = find_tokens_for(n, cfg)
        if tokens:
            log.info("Narrative @%s %s -> %s", n.get("author"), n["keywords"], [t[0]["symbol"] for t in tokens])
        if tokens:  # only the LEADER (exact name + most volume) -> no spam from copies
            m, match = tokens[0]
            evaluate(m, cfg, store, tg, n, match, stats, lane="narrative", copies=copies)
        # "narrative only" alerts need Claude's judgement, otherwise every Musk tweet would alert
        if n.get("pid") in new_ids and not tokens and n.get("judged") \
                and cfg.get("alerts", {}).get("narrative_only_alerts") \
                and (n.get("tier") == 1 or n["weight"] >= 1.2):
            key = "narr:" + "|".join(sorted(k.lower() for k in n["keywords"]))
            if not store.seen(key):
                tg.send(alerts.narrative_alert(n))
                store.mark_seen(key)


# ---------------------------------------------------------------- agent 2
def onchain_agent(cfg: dict, store, tg, stats: dict) -> None:
    log.info("== Agent 2: on-chain ==")
    oc = cfg.get("onchain", {})
    discovered = dex.latest_solana_tokens() if oc.get("enabled") else []
    watched = store.watched(cfg.get("pumpfun", {}).get("watch_hours", 6))
    mints = list(dict.fromkeys(discovered + list(watched)))
    if not mints:
        return
    mc = cfg.get("matching", {})
    active = store.active_narratives(mc.get("narrative_ttl_hours", 24))
    by_pid = {n.get("pid"): n for n in active}
    for mint, p in dex.best_pair_per_token(dex.pairs_for_tokens(mints)).items():
        m = dex.metrics(p)
        if mint in watched and by_pid.get(watched[mint]["pid"]):
            n, match, lane = by_pid[watched[mint]["pid"]], 1.0, "pumpfun"
        else:
            n, match = best_narrative_for(m, active)
            lane = "narrative" if n else scoring.onchain_lane(m, oc)
        if n and scoring.narrative_filter(m, mc):
            continue
        if not n and not lane:
            continue
        evaluate(m, cfg, store, tg, n, match, stats, lane=lane)
    stats["onchain_checked"] = len(mints)


# ---------------------------------------------------------------- digest
def maybe_digest(cfg: dict, store, tg) -> None:
    ac = cfg.get("alerts", {})
    every = ac.get("digest_every_minutes", 30)
    if not every or not store.digest_due(every):
        return
    events = store.pop_events()
    if not events and not ac.get("digest_send_empty", False):
        return
    tg.send(alerts.digest(events, int(every), ac.get("timezone", "Europe/Athens")))
