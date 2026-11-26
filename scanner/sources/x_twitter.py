"""X / Twitter via twitterapi.io (pay per tweet returned, ~$0.15 / 1000 tweets).

- ONE advanced_search per chunk of accounts with `since_time:` -> we only pay for new tweets.
- Replies, retweets and quotes are KEPT (Musk's 3-word reply to Asteroid made a 68,000% coin).
- For replies we fetch the tweet he replied to, so the alert can explain the context.
"""
import logging
import re
import time
from datetime import datetime

from ..http import get_json

log = logging.getLogger("scanner")
BASE = "https://api.twitterapi.io"
SOL_MINT_RE = re.compile(r"\b([1-9A-HJ-NP-Za-km-z]{32,44})\b")


def _parse_time(s) -> float:
    try:
        return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y").timestamp()
    except (TypeError, ValueError):
        return time.time()


def find_mints(text: str) -> list[str]:
    """Solana contract addresses inside a text (pump.fun ones end with 'pump')."""
    out = []
    for m in SOL_MINT_RE.findall(text or ""):
        if m.endswith("pump") or (len(m) >= 43 and any(c.isdigit() for c in m)):
            if m not in out:
                out.append(m)
    return out


def _norm(t: dict, tier: int) -> dict:
    a = t.get("author") or {}
    kind, context, ctx_author = "post", "", ""
    if t.get("isRetweet") and t.get("retweeted_tweet"):
        rt = t["retweeted_tweet"]
        kind, context = "retweet", rt.get("text") or ""
        ctx_author = (rt.get("author") or {}).get("userName") or ""
    elif t.get("isQuote") and t.get("quoted_tweet"):
        q = t["quoted_tweet"]
        kind, context = "quote", q.get("text") or ""
        ctx_author = (q.get("author") or {}).get("userName") or ""
    elif t.get("isReply"):
        kind = "reply"
        ctx_author = t.get("inReplyToUsername") or ""
    text = t.get("text") or ""
    return {
        "source": "x",
        "id": f"x:{t.get('id')}",
        "kind": kind,
        "reply_to_id": t.get("inReplyToId") if kind == "reply" else None,
        "context": context,
        "context_author": ctx_author,
        "text": text,
        "url": t.get("url") or "",
        "author": a.get("userName") or "",
        "followers": a.get("followers") or 0,
        "likes": t.get("likeCount") or 0,
        "retweets": t.get("retweetCount") or 0,
        "views": t.get("viewCount") or 0,
        "ts": _parse_time(t.get("createdAt")),
        "tier": tier,
        "mints": find_mints(text + " " + context),
    }


def _search(key: str, query: str, max_pages: int = 3, qtype: str = "Latest") -> list[dict]:
    out, cursor = [], ""
    for _ in range(max_pages):
        data = get_json(f"{BASE}/twitter/tweet/advanced_search",
                        params={"query": query, "queryType": qtype, "cursor": cursor},
                        headers={"X-API-Key": key})
        if not data:
            break
        if data.get("status") == "error":
            log.warning("X API error: %s", str(data.get("msg") or data.get("message"))[:200])
            break
        tweets = data.get("tweets") or (data.get("data") or {}).get("tweets") or []
        out.extend(tweets)
        if not data.get("has_next_page") or not data.get("next_cursor"):
            break
        cursor = data["next_cursor"]
    return out


def tweets_by_ids(key: str, ids: list[str]) -> dict[str, dict]:
    out = {}
    for i in range(0, len(ids), 50):
        data = get_json(f"{BASE}/twitter/tweets", params={"tweet_ids": ",".join(ids[i:i + 50])},
                        headers={"X-API-Key": key}) or {}
        for t in data.get("tweets") or []:
            out[str(t.get("id"))] = t
    return out


def fetch(cfg: dict, key: str, since_ts: float) -> list[dict]:
    if not key:
        log.info("X: no TWITTERAPI_IO_KEY — skipped")
        return []
    xc = cfg.get("x", {})
    since = int(since_ts)
    posts = []
    tier1 = set(u.lower() for u in xc.get("tier1", []))
    replies = "" if xc.get("include_replies", True) else " -filter:replies"

    accounts = list(xc.get("tier1", [])) + list(xc.get("tier2", []))
    for i in range(0, len(accounts), 12):  # query length limit
        chunk = accounts[i:i + 12]
        q = "(" + " OR ".join(f"from:{u}" for u in chunk) + f") since_time:{since}{replies}"
        for t in _search(key, q):
            author = ((t.get("author") or {}).get("userName") or "").lower()
            posts.append(_norm(t, 1 if author in tier1 else 2))

    vs = xc.get("viral_search") or {}
    if vs.get("enabled"):
        q = f"{vs.get('query', 'memecoin')} min_faves:{int(vs.get('min_likes', 1000))} since_time:{since}"
        for t in _search(key, q, max_pages=1):
            posts.append(_norm(t, 3))

    # context for replies: what did they reply to?
    need = [p["reply_to_id"] for p in posts if p["kind"] == "reply" and p["reply_to_id"]]
    if need:
        parents = tweets_by_ids(key, list(dict.fromkeys(need)))
        for p in posts:
            par = parents.get(str(p.get("reply_to_id")))
            if par:
                p["context"] = par.get("text") or ""
                p["context_author"] = (par.get("author") or {}).get("userName") or p["context_author"]
                p["mints"] = list(dict.fromkeys(p["mints"] + find_mints(p["context"])))
    log.info("X: %d new posts (%d replies)", len(posts), len(need))
    return posts


def mentions(key: str, symbol: str, mint: str, limit: int = 6) -> list[dict]:
    """Top tweets talking about a coin (to find WHY it exists / who pushes it)."""
    if not key:
        return []
    q = f'"{mint}" OR ${symbol}' if symbol and symbol.isalnum() and len(symbol) <= 10 else f'"{mint}"'
    tweets = _search(key, q, max_pages=1, qtype="Top")
    tweets.sort(key=lambda t: (t.get("likeCount") or 0), reverse=True)
    return [_norm(t, 2) for t in tweets[:limit]]


def check(key: str) -> str:
    """Connection test for --test-telegram: read @elonmusk's latest tweet."""
    if not key:
        return "✗ (λείπει TWITTERAPI_IO_KEY)"
    data = get_json(f"{BASE}/twitter/user/last_tweets", params={"userName": "elonmusk"},
                    headers={"X-API-Key": key})
    if not data:
        return "✗ (το key δεν δουλεύει ή δεν έχει credits)"
    if data.get("status") == "error":
        return f"✗ ({str(data.get('msg'))[:80]})"
    tweets = (data.get("data") or {}).get("tweets") or data.get("tweets") or []
    if not tweets:
        return "✓ συνδέθηκε (κανένα tweet δεν επιστράφηκε)"
    age_min = (time.time() - _parse_time(tweets[0].get("createdAt"))) / 60
    return f"✓ (τελευταίο tweet @elonmusk πριν {age_min:.0f} λεπτά)"


def account_age_days(key: str, handle: str) -> float | None:
    """Age of an X account in days (used for the 'fresh account = rug' rule)."""
    if not key or not handle:
        return None
    data = get_json(f"{BASE}/twitter/user/info", params={"userName": handle},
                    headers={"X-API-Key": key})
    created = ((data or {}).get("data") or {}).get("createdAt")
    if not created:
        return None
    try:
        ts = datetime.strptime(created, "%a %b %d %H:%M:%S %z %Y").timestamp()
    except ValueError:
        try:
            ts = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return (time.time() - ts) / 86400
