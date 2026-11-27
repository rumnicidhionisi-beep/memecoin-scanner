"""Watch famous X accounts for the quiet signals early traders use:

- PROFILE changes: name / bio / profile picture (e.g. Musk renaming himself "Kekius Maximus").
- NEW FOLLOWS: "the follow comes first, the news comes later".

Costs (twitterapi.io): user/info ~$0.00018 per account, followings ~$0.0006 per call (20 newest).
"""
import logging

from ..http import get_json

log = logging.getLogger("scanner")
BASE = "https://api.twitterapi.io"
CRYPTO_HINTS = ("$", "coin", "token", "sol", "pump", "meme", " ca", "ca:", "crypto", "nft", "degen", "ticker")


def profile(key: str, handle: str) -> dict | None:
    data = get_json(f"{BASE}/twitter/user/info", params={"userName": handle},
                    headers={"X-API-Key": key}, retries=1)
    d = (data or {}).get("data") or {}
    if not d:
        return None
    return {"name": d.get("name") or "", "description": d.get("description") or "",
            "picture": d.get("profilePicture") or ""}


def profile_changes(old: dict | None, new: dict) -> list[str]:
    if not old:
        return []
    out = []
    if old.get("name") != new.get("name"):
        out.append(f"όνομα: «{old.get('name')}» → «{new.get('name')}»")
    if old.get("description") != new.get("description"):
        out.append(f"bio: «{(new.get('description') or '')[:160]}»")
    if old.get("picture") and new.get("picture") and old["picture"] != new["picture"]:
        out.append("νέα φωτογραφία προφίλ")
    return out


def recent_followings(key: str, handle: str, n: int = 20) -> list[dict]:
    data = get_json(f"{BASE}/twitter/user/followings", params={"userName": handle, "pageSize": n},
                    headers={"X-API-Key": key}, retries=1)
    out = []
    for u in (data or {}).get("followings") or []:
        h = u.get("userName") or u.get("screen_name")
        if h:
            out.append({"handle": h, "name": u.get("name") or "", "bio": u.get("description") or "",
                        "followers": u.get("followers_count") or 0, "created": u.get("created_at") or ""})
    return out


def new_follows(known: list[str] | None, latest: list[dict]) -> list[dict]:
    """First time we see an account: baseline only (no alerts)."""
    if known is None:
        return []
    seen = set(h.lower() for h in known)
    return [u for u in latest if u["handle"].lower() not in seen]


def looks_crypto(u: dict) -> bool:
    text = f" {u.get('name', '')} {u.get('bio', '')} ".lower()
    return any(h in text for h in CRYPTO_HINTS)
