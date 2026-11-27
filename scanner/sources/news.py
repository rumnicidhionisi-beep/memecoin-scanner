"""News headlines from RSS feeds (free)."""
import hashlib
import re
import logging
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from ..http import get_text

log = logging.getLogger("scanner")


def _ts(s: str | None) -> float:
    try:
        return parsedate_to_datetime(s).timestamp()
    except (TypeError, ValueError):
        return time.time()


def parse_rss(xml_text: str) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
        desc = re.sub(r"\s+", " ", desc).strip()
        out.append({"title": title, "link": link, "desc": desc, "ts": _ts(item.findtext("pubDate"))})
    return out


def fetch(cfg: dict, max_age_hours: float = 6) -> list[dict]:
    nc = cfg.get("news", {})
    words = [w.lower() for w in nc.get("must_contain_any", [])]
    cutoff = time.time() - max_age_hours * 3600
    out = []
    for feed in nc.get("feeds", []) + nc.get("truth_social", []):
        txt = get_text(feed)
        if not txt:
            continue
        truth = "trumpstruth" in feed
        for it in parse_rss(txt):
            hay = (it["title"] + " " + it["desc"]).lower()
            if it["ts"] < cutoff:
                continue
            if truth:  # every Trump post counts, no keyword filter
                text = it["desc"] if len(it["desc"]) > len(it["title"]) else it["title"]
                if not text or text.lower().startswith(("image post", "video post")) and len(text) < 40:
                    continue
                out.append({
                    "source": "truth", "id": "truth:" + hashlib.sha1((it["link"] or text).encode()).hexdigest()[:16],
                    "text": text[:500], "url": it["link"], "author": "realDonaldTrump (Truth Social)",
                    "followers": 10_000_000, "likes": 0, "retweets": 0, "views": 0, "ts": it["ts"],
                    "tier": 1, "kind": "post", "context": "", "context_author": "", "mints": [],
                })
                continue
            if words and not any(w in hay for w in words):
                continue
            out.append({
                "source": "news",
                "id": "news:" + hashlib.sha1((it["link"] or it["title"]).encode()).hexdigest()[:16],
                "text": it["title"],
                "url": it["link"],
                "author": feed.split("/")[2],
                "followers": 0, "likes": 0, "retweets": 0, "views": 0,
                "ts": it["ts"],
                "tier": 2,
                "kind": "news", "context": it["desc"][:300], "context_author": "", "mints": [],
            })
    log.info("News: %d relevant headlines", len(out))
    return out
