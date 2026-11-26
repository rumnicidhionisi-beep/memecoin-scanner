"""RugCheck public API (free, no key).

Full report -> the same checks you do in Padre: top-10 holders %, dev (creator) holding %,
insiders % / count, mint & freeze authority, rug score. Pool / bonding-curve accounts are
excluded from the top-10 so the number means "real wallets".
"""
from ..http import get_json

BASE = "https://api.rugcheck.xyz/v1"


def _strings(obj, out: set, depth=0):
    if depth > 3:
        return
    if isinstance(obj, str) and len(obj) >= 32:
        out.add(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _strings(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _strings(v, out, depth + 1)


def _from_report(data: dict) -> dict:
    risks = data.get("risks") or []
    names = [str(r.get("name", "")) for r in risks if isinstance(r, dict)]
    danger = [str(r.get("name", "")) for r in risks
              if isinstance(r, dict) and str(r.get("level", "")).lower() == "danger"]
    # accounts that belong to pools / bonding curve -> not real holders
    pool_accounts: set = set()
    for mk in data.get("markets") or []:
        if isinstance(mk, dict):
            _strings({k: v for k, v in mk.items() if k != "lp"}, pool_accounts)
    holders = [h for h in data.get("topHolders") or [] if isinstance(h, dict)
               and h.get("address") not in pool_accounts and h.get("owner") not in pool_accounts]
    top10 = sum(float(h.get("pct") or 0) for h in holders[:10]) if holders else None
    insiders = [h for h in holders if h.get("insider")]
    token = data.get("token") or {}
    supply, dec = token.get("supply"), token.get("decimals")
    dev_pct = None
    cb = data.get("creatorBalance")
    if cb is not None and supply:
        try:
            ui_supply = float(supply) / (10 ** int(dec or 0))
            dev_pct = (float(cb) / float(supply) * 100) if float(cb) > ui_supply else (float(cb) / ui_supply * 100)
        except (TypeError, ValueError, ZeroDivisionError):
            dev_pct = None
    return {
        "score": data.get("score_normalised", data.get("score")),
        "risks": names,
        "danger": danger,
        "mint_authority": bool(data.get("mintAuthority")),
        "freeze_authority": bool(data.get("freezeAuthority")),
        "top10_pct": round(top10, 1) if top10 is not None else None,
        "dev_pct": round(dev_pct, 2) if dev_pct is not None else None,
        "insiders_pct": round(sum(float(h.get("pct") or 0) for h in insiders), 1),
        "insiders_count": len(insiders),
        "insider_networks": data.get("graphInsidersDetected"),
        "rugged": bool(data.get("rugged")),
        "full": True,
    }


def _from_summary(data: dict) -> dict:
    risks = data.get("risks") or []
    names = [str(r.get("name", "")) for r in risks if isinstance(r, dict)]
    danger = [str(r.get("name", "")) for r in risks
              if isinstance(r, dict) and str(r.get("level", "")).lower() == "danger"]
    low = " | ".join(names).lower()
    top10 = None
    for r in risks:
        if isinstance(r, dict) and "top 10" in str(r.get("name", "")).lower():
            try:
                top10 = float(str(r.get("value", "")).replace("%", "").strip())
            except ValueError:
                pass
    return {
        "score": data.get("score_normalised", data.get("score")),
        "risks": names, "danger": danger,
        "mint_authority": "mint authority" in low, "freeze_authority": "freeze authority" in low,
        "top10_pct": top10, "dev_pct": None, "insiders_pct": None, "insiders_count": None,
        "insider_networks": None, "rugged": bool(data.get("rugged")), "full": False,
    }


def summary(mint: str) -> dict | None:
    data = get_json(f"{BASE}/tokens/{mint}/report", timeout=30, retries=1)
    if isinstance(data, dict) and (data.get("topHolders") is not None or data.get("score") is not None):
        return _from_report(data)
    data = get_json(f"{BASE}/tokens/{mint}/report/summary", timeout=15, retries=1)
    return _from_summary(data) if isinstance(data, dict) else None
