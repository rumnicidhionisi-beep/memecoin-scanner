"""Small JSON state file: what we've seen / alerted, active narratives, digest events, watchlist."""
import json
import time
from pathlib import Path

from .config import ROOT

STATE_FILE = ROOT / "state" / "state.json"


class Store:
    def __init__(self, path: Path = STATE_FILE):
        self.path = path
        self.data = {
            "seen": {},          # key -> ts   (tweet ids, video ids, news links)
            "alerted": {},       # mint -> {"ts":..., "score":...}
            "narratives": [],    # [{keywords, source, text, url, weight, ts}]
            "events": [],        # for the 30' digest
            "watch": {},         # pump.fun mints born on a narrative -> {"pid", "ts"}
            "cursors": {},       # e.g. last X since_time, last TikTok run
        }
        if path.exists():
            try:
                self.data.update(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass

    # --- generic -------------------------------------------------
    def seen(self, key: str) -> bool:
        return key in self.data["seen"]

    def mark_seen(self, key: str) -> None:
        self.data["seen"][key] = time.time()

    def cursor(self, name: str, default=None):
        return self.data["cursors"].get(name, default)

    def set_cursor(self, name: str, value) -> None:
        self.data["cursors"][name] = value

    # --- alerts --------------------------------------------------
    def should_alert(self, mint: str, score: float, cooldown_h: float = 6) -> bool:
        """Alert once per mint, again only if score jumps by 15+ after cooldown."""
        prev = self.data["alerted"].get(mint)
        if not prev:
            return True
        if time.time() - prev["ts"] < cooldown_h * 3600:
            return False
        return score >= prev["score"] + 15

    def mark_alerted(self, mint: str, score: float) -> None:
        self.data["alerted"][mint] = {"ts": time.time(), "score": score}

    # --- narratives ----------------------------------------------
    def add_narrative(self, n: dict) -> None:
        n = dict(n, ts=n.get("ts") or time.time())
        self.data["narratives"].append(n)

    def active_narratives(self, ttl_hours: float) -> list[dict]:
        cutoff = time.time() - ttl_hours * 3600
        return [n for n in self.data["narratives"] if n["ts"] >= cutoff]

    # --- digest --------------------------------------------------
    def add_event(self, e: dict) -> None:
        self.data["events"].append(dict(e, ts=time.time()))

    def digest_due(self, every_min: float) -> bool:
        last = self.cursor("digest_last")
        if last is None:  # first run: start the clock, don't send
            self.set_cursor("digest_last", time.time())
            return False
        return time.time() - last >= every_min * 60 - 30

    def pop_events(self) -> list[dict]:
        ev, self.data["events"] = self.data["events"], []
        self.set_cursor("digest_last", time.time())
        return ev

    # --- pump.fun watchlist ---------------------------------------
    def watch(self, mint: str, pid: str) -> None:
        self.data["watch"].setdefault(mint, {"pid": pid, "ts": time.time()})

    def watched(self, hours: float) -> dict:
        cutoff = time.time() - hours * 3600
        return {m: v for m, v in self.data["watch"].items() if v["ts"] >= cutoff}

    # --- persistence ---------------------------------------------
    def prune(self) -> None:
        now = time.time()
        self.data["seen"] = {k: t for k, t in self.data["seen"].items() if now - t < 3 * 86400}
        self.data["alerted"] = {k: v for k, v in self.data["alerted"].items() if now - v["ts"] < 7 * 86400}
        self.data["narratives"] = [n for n in self.data["narratives"] if now - n["ts"] < 3 * 86400]
        self.data["events"] = [e for e in self.data["events"] if now - e["ts"] < 86400][-300:]
        self.data["watch"] = {m: v for m, v in self.data["watch"].items() if now - v["ts"] < 86400}

    def save(self) -> None:
        self.prune()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
