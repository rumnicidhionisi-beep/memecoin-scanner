"""RugCheck public API (free, no key)."""
from ..http import get_json

BASE = "https://api.rugcheck.xyz/v1"


def summary(mint: str) -> dict | None:
    data = get_json(f"{BASE}/tokens/{mint}/report/summary", timeout=15, retries=1)
    if not isinstance(data, dict):
        return None
    risks = data.get("risks") or []
    names = [str(r.get("name", "")) for r in risks if isinstance(r, dict)]
    danger = [str(r.get("name", "")) for r in risks
              if isinstance(r, dict) and str(r.get("level", "")).lower() == "danger"]
    low = " | ".join(names).lower()
    top10 = None
    for r in risks:
        if isinstance(r, dict) and "top 10" in str(r.get("name", "")).lower():
            val = str(r.get("value", "")).replace("%", "").strip()
            try:
                top10 = float(val)
            except ValueError:
                pass
    return {
        "score": data.get("score_normalised", data.get("score")),
        "risks": names,
        "danger": danger,
        "mint_authority": "mint authority" in low,
        "freeze_authority": "freeze authority" in low,
        "top10_pct": top10,
        "lp_locked_pct": data.get("lpLockedPct"),
        "rugged": bool(data.get("rugged")),
    }
