"""TikTok via Apify (clockworks/tiktok-hashtag-scraper, ~$2–5 / 1000 videos)."""
import logging
import time
from datetime import datetime

from ..http import post_json
from .x_twitter import find_mints

log = logging.getLogger("scanner")
ACTOR = "clockworks~tiktok-hashtag-scraper"


def _ts(v: dict) -> float:
    if v.get("createTime"):
        try:
            return float(v["createTime"])
        except (TypeError, ValueError):
            pass
    iso = v.get("createTimeISO")
    if iso:
        try:
            return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return time.time()


def fetch(cfg: dict, token: str) -> list[dict]:
    tc = cfg.get("tiktok", {})
    if not token:
        log.info("TikTok: no APIFY_TOKEN — skipped")
        return []
    items = post_json(
        f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items?token={token}",
        {"hashtags": tc.get("hashtags", ["memecoin"]),
         "resultsPerPage": int(tc.get("results_per_hashtag", 20)),
         "shouldDownloadVideos": False, "shouldDownloadCovers": False,
         "shouldDownloadSubtitles": False, "shouldDownloadSlideshowImages": False},
        timeout=300,
    ) or []
    min_views = int(tc.get("min_views", 20000))
    cutoff = time.time() - float(tc.get("max_age_hours", 24)) * 3600
    out = []
    for v in items if isinstance(items, list) else []:
        views = v.get("playCount") or 0
        ts = _ts(v)
        if views < min_views or ts < cutoff:
            continue
        tags = " ".join("#" + (h.get("name") or "") for h in (v.get("hashtags") or []) if isinstance(h, dict))
        out.append({
            "source": "tiktok",
            "id": f"tt:{v.get('id')}",
            "text": f"{v.get('text') or ''} {tags}".strip(),
            "url": v.get("webVideoUrl") or "",
            "author": (v.get("authorMeta") or {}).get("name") or "",
            "followers": (v.get("authorMeta") or {}).get("fans") or 0,
            "likes": v.get("diggCount") or 0,
            "retweets": v.get("shareCount") or 0,
            "views": views,
            "ts": ts,
            "tier": 2,
            "kind": "video", "context": "", "context_author": "",
            "mints": find_mints(v.get("text") or ""),
        })
    log.info("TikTok: %d viral videos", len(out))
    return out
