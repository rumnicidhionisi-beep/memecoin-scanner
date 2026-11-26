"""DexScreener public API (free). Limits: profiles/boosts 60 rpm, search/pairs 300 rpm."""
import time

from ..http import get_json

BASE = "https://api.dexscreener.com"
STABLE_OR_SOL = {
    "So11111111111111111111111111111111111111112",   # wSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}


def search(q: str) -> list[dict]:
    data = get_json(f"{BASE}/latest/dex/search", params={"q": q}) or {}
    return [p for p in data.get("pairs") or [] if p.get("chainId") == "solana"]


def pairs_for_tokens(addresses: list[str]) -> list[dict]:
    out = []
    for i in range(0, len(addresses), 30):
        chunk = ",".join(addresses[i:i + 30])
        data = get_json(f"{BASE}/tokens/v1/solana/{chunk}")
        if isinstance(data, list):
            out.extend(data)
    return out


def latest_solana_tokens() -> list[str]:
    """New DexScreener profiles + latest boosts (these are paid listings: a dev who pays
    for a profile/boost is at least investing in the coin)."""
    addrs = []
    for path in ("/token-profiles/latest/v1", "/token-boosts/latest/v1", "/token-boosts/top/v1"):
        data = get_json(BASE + path)
        for t in data if isinstance(data, list) else []:
            if t.get("chainId") == "solana" and t.get("tokenAddress"):
                addrs.append(t["tokenAddress"])
    return list(dict.fromkeys(addrs))


def best_pair_per_token(pairs: list[dict]) -> dict[str, dict]:
    """Group pairs by token. Keep the most liquid pair, but remember the OLDEST pair
    creation time = the real age of the coin (not just of a new pool)."""
    by_token: dict[str, dict] = {}
    oldest: dict[str, int] = {}
    for p in pairs:
        addr = (p.get("baseToken") or {}).get("address")
        if not addr or addr in STABLE_OR_SOL:
            continue
        created = p.get("pairCreatedAt") or 0
        if created and (addr not in oldest or created < oldest[addr]):
            oldest[addr] = created
        liq = (p.get("liquidity") or {}).get("usd") or 0
        cur = by_token.get(addr)
        if cur is None or liq > ((cur.get("liquidity") or {}).get("usd") or 0):
            by_token[addr] = p
    return {a: dict(p, _oldest=oldest.get(a)) for a, p in by_token.items()}


def metrics(p: dict) -> dict:
    """Flatten a DexScreener pair into the numbers we score on."""
    vol = p.get("volume") or {}
    tx = p.get("txns") or {}
    h1 = tx.get("h1") or {}
    h24 = tx.get("h24") or {}
    m5 = tx.get("m5") or {}
    liq = (p.get("liquidity") or {}).get("usd") or 0
    created_ms = p.get("_oldest") or p.get("pairCreatedAt") or 0
    age_h = (time.time() * 1000 - created_ms) / 3_600_000 if created_ms else None
    tx1h = (h1.get("buys") or 0) + (h1.get("sells") or 0)
    v1h = vol.get("h1") or 0
    v24 = vol.get("h24") or 0
    info = p.get("info") or {}
    socials = {s.get("type"): s.get("url") for s in info.get("socials") or [] if isinstance(s, dict)}
    base = p.get("baseToken") or {}
    return {
        "mint": base.get("address"),
        "symbol": base.get("symbol") or "",
        "name": base.get("name") or "",
        "price": p.get("priceUsd"),
        "mc": p.get("marketCap") or p.get("fdv") or 0,
        "liq": liq,
        "age_h": age_h,
        "vol_m5": vol.get("m5") or 0,
        "vol_1h": v1h,
        "vol_6h": vol.get("h6") or 0,
        "vol_24h": v24,
        "tx_1h": tx1h,
        "buys_1h": h1.get("buys") or 0,
        "sells_1h": h1.get("sells") or 0,
        "buys_m5": m5.get("buys") or 0,
        "sells_m5": m5.get("sells") or 0,
        "buys_24h": h24.get("buys") or 0,
        "sells_24h": h24.get("sells") or 0,
        "avg_trade_1h": (v1h / tx1h) if tx1h else 0,
        "vol_liq_24h": (v24 / liq) if liq else 0,
        "chg_m5": (p.get("priceChange") or {}).get("m5") or 0,
        "chg_1h": (p.get("priceChange") or {}).get("h1") or 0,
        "chg_24h": (p.get("priceChange") or {}).get("h24") or 0,
        "dex": p.get("dexId") or "",
        "url": p.get("url") or "",
        "twitter": socials.get("twitter") or "",
        "telegram": socials.get("telegram") or "",
        "has_profile": bool(info),
    }
