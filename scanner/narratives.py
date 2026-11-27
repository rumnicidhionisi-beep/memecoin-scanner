"""Turn posts (tweets, TikToks, headlines) into 'narratives' = keywords a memecoin could be named after.

With ANTHROPIC_API_KEY: Claude reads the posts and picks the meme-able words.
Without it: a rule-based fallback ($TICKERS, #hashtags, Capitalised names, "quoted phrases").
"""
import json
import logging
import math
import re

from .http import post_json

log = logging.getLogger("scanner")

STOP = set("""
a an the and or but if then so to of in on at for with from by is are was were be been it its this that these those
i you he she we they me my your our their his her them us rt via just now new today will can not no yes all more most
very really what when where who why how get got go going make made one two three amp https http com www co
breaking news update crypto coin coins token tokens memecoin memecoins meme memes solana sol pump pumpfun fun
bitcoin btc eth ethereum price market markets trading trade buy sell holders chart dex fyp foryou viral tiktok
mr ms said says say here there about after before over under up down out off day week year time people like
elon musk donald trump jd vance vitalik buterin changpeng zhao binance saylor jensen huang nvidia tesla spacex
white house president senate congress sec fed reuters bloomberg cnbc cointelegraph decrypt google says report
""".split())

GENERIC_TICKERS = {"BTC", "ETH", "SOL", "USDC", "USDT", "BNB", "XRP", "DOGE", "USD", "AI", "CEO", "US", "USA"}

PROMPT = """You are a Solana memecoin narrative analyst. Below are recent posts (tweets/replies/retweets
from famous accounts, viral TikToks, news headlines). A post may include CONTEXT = the tweet it replies
to / retweets / quotes. For EACH post decide if it could spawn or pump a memecoin (a funny phrase, an
animal/pet name, a nickname, a new meme, a celebrity reacting to a story, an explicit $ticker or
contract). Celebrity REPLIES to small accounts are often the strongest signal. Ignore generic market talk.

Return ONLY JSON: a list of objects {"i": <post index>, "meme": true/false, "strength": 1-10,
"keywords": [1-4 short words/phrases a coin would be NAMED, e.g. "asteroid", "kekius", "broccoli"],
"why": "<max 12 words, in Greek>"}.
Include EVERY post index. Keep it compact: no markdown, no text outside the JSON list.

POSTS:
"""


def post_weight(p: dict) -> float:
    import time as _t
    base = {1: 1.0, 2: 0.6, 3: 0.5}.get(p.get("tier", 2), 0.5)
    if p["source"] == "news":
        base = 0.4
    if p["source"] == "truth":
        base = 1.3  # Trump on Truth Social often posts before X
    eng = (p.get("likes") or 0) + 3 * (p.get("retweets") or 0) + (p.get("views") or 0) / 100
    boost = min(1.0, math.log10(1 + eng) / 6)  # ~1M engagement -> +1.0
    # engagement VELOCITY (likes per minute) matters more than the total
    age_min = max(1.0, (_t.time() - (p.get("ts") or _t.time())) / 60)
    vel = (p.get("likes") or 0) / age_min
    boost += min(0.5, math.log10(1 + vel) / 4)  # ~100 likes/min -> +0.5
    return round(min(2.0, base + boost), 3)


def heuristic_keywords(text: str) -> list[str]:
    kws = []
    kws += [t.upper() for t in re.findall(r"\$([A-Za-z][A-Za-z0-9]{1,11})\b", text)]
    kws += [h.lower() for h in re.findall(r"#([A-Za-z][A-Za-z0-9_]{2,25})", text)]
    kws += [q.strip().lower() for q in re.findall(r'"([^"]{3,30})"', text)]
    for w in re.findall(r"\b([A-Z][a-z]{2,15})\b", text):
        kws.append(w.lower())
    out = []
    for k in kws:
        if k.lower() in STOP or k.upper() in GENERIC_TICKERS or len(k) < 3:
            continue
        if k not in out:
            out.append(k)
    return out[:5]


def parse_json_list(text: str) -> list | None:
    """Robust: handles ```json fences, extra prose, and truncated output (salvages complete objects)."""
    text = re.sub(r"```(?:json)?", "", text or "")
    m = re.search(r"\[.*\]", text, re.S)
    if m:
        try:
            out = json.loads(m.group(0))
            if isinstance(out, list):
                return out
        except ValueError:
            pass
    objs = []
    for chunk in re.findall(r"\{[^{}]*\}", text, re.S):
        try:
            objs.append(json.loads(chunk))
        except ValueError:
            continue
    return objs or None


def _claude(posts: list[dict], key: str, model: str) -> list[dict] | None:
    lines = []
    for i, p in enumerate(posts):
        line = f"[{i}] ({p['source']}/{p.get('kind','post')} @{p.get('author','')}) {p['text'][:300]}"
        if p.get("context"):
            line += f"\n    CONTEXT (@{p.get('context_author','')}): {p['context'][:250]}"
        lines.append(line)
    res = post_json(
        "https://api.anthropic.com/v1/messages",
        {"model": model, "max_tokens": 4000,
         "messages": [{"role": "user", "content": PROMPT + "\n".join(lines)}]},
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        timeout=120,
    )
    if not res:
        return None
    text = "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text")
    out = parse_json_list(text)
    if out is None:
        log.warning("Claude reply not parseable (stop=%s): %s", res.get("stop_reason"), text[:300].replace("\n", " "))
    return out


def extract(posts: list[dict], anthropic_key: str = "", model: str = "claude-haiku-5-5") -> list[dict]:
    """Return narratives: {keywords, source, author, text, url, weight, why}."""
    if not posts:
        return []
    narratives = []
    judged = None
    if anthropic_key:
        judged = {}
        for i in range(0, len(posts), 20):
            batch = posts[i:i + 20]
            out = _claude(batch, anthropic_key, model)
            if out is None:
                judged = None
                break
            for o in out:
                if isinstance(o, dict) and isinstance(o.get("i"), int):
                    judged[i + o["i"]] = o
        if judged is None:
            log.warning("Claude analysis failed — using rule-based keywords")

    for idx, p in enumerate(posts):
        w = post_weight(p)
        if judged is not None:
            j = judged.get(idx)
            if (not j or not j.get("meme")) and not p.get("mints"):
                continue
            j = j or {}
            kws = [str(k).strip() for k in j.get("keywords", []) if str(k).strip()]
            try:
                strength = min(10, int(j.get("strength", 5)))
            except (TypeError, ValueError):
                strength = 5
            w = round(w * (0.5 + strength / 10), 3)
            why = j.get("why", "")
        else:
            kws = heuristic_keywords(p["text"] + " " + (p.get("context") or ""))
            why = ""
        if p.get("mints"):  # an explicit contract address is the strongest link
            w = round(w + 0.3, 3)
        if not kws and not p.get("mints"):
            continue
        narratives.append({
            "keywords": kws[:4], "source": p["source"], "author": p.get("author", ""),
            "text": p["text"][:280], "url": p.get("url", ""), "weight": w, "why": why,
            "tier": p.get("tier", 2), "ts": p.get("ts"), "pid": p["id"],
            "judged": judged is not None,
            "kind": p.get("kind", "post"), "context": (p.get("context") or "")[:280],
            "context_author": p.get("context_author", ""), "mints": p.get("mints", []),
        })
    log.info("Narratives: %d from %d posts", len(narratives), len(posts))
    return narratives


def normalise(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def keyword_matches_token(keyword: str, symbol: str, name: str) -> float:
    """0..1 how well a narrative keyword matches a token. Exact symbol/name = 1."""
    k = normalise(keyword.lstrip("$#"))
    if len(k) < 3:
        return 0.0
    sym, nm = normalise(symbol), normalise(name)
    if k == sym or k == nm:
        return 1.0
    if len(k) >= 4 and (k in nm or k in sym):
        return 0.7
    if len(sym) >= 4 and sym in k:
        return 0.5
    return 0.0
