"""Entry point.

  python -m scanner.main --once              # one full scan (GitHub Actions / cron)
  python -m scanner.main --once --listen 120 # + 2 min listening to new pump.fun coins
  python -m scanner.main --live              # always-on server: X every 60s, full scan every 5 min,
                                             #   every new pump.fun coin in real time
  python -m scanner.main --once --dry-run    # print messages instead of sending
  python -m scanner.main --test-telegram     # send a test message
"""
import argparse
import asyncio
import logging
import time

from . import agents, alerts, narratives as nar
from .config import load_config
from .sources import pumpportal, x_twitter
from .store import Store
from .telegram import Telegram

log = logging.getLogger("scanner")


def run_once(cfg, store, tg, fast: bool = False) -> dict:
    """fast=True: X only + fresh narratives + on-chain (used every minute in live mode)."""
    stats = {}
    try:
        if fast:
            agents.narrative_agent(cfg, store, tg, stats, sources=("x",), recheck_older=False)
        else:
            agents.narrative_agent(cfg, store, tg, stats)
    except Exception:  # one source failing must never kill the whole run
        log.exception("narrative agent failed")
    try:
        agents.onchain_agent(cfg, store, tg, stats)
    except Exception:
        log.exception("on-chain agent failed")
    try:
        agents.maybe_digest(cfg, store, tg)
    except Exception:
        log.exception("digest failed")
    store.save()
    log.info("Run stats: %s", stats)
    return stats


class Listener:
    """New pump.fun coin whose name matches a FRESH narrative -> watchlist (+ optional ⚡ early warning)."""

    def __init__(self, cfg, store, tg):
        self.cfg, self.store, self.tg = cfg, store, tg
        self.hits: dict[str, int] = {}
        self.pending: list[tuple[str, str]] = []  # (mint, pid) -> added to the store by the main loop
        self.refresh()

    def refresh(self):
        mc = self.cfg.get("matching", {})
        cutoff = time.time() - mc.get("watch_minutes", 90) * 60 * 2
        self.active = [n for n in self.store.active_narratives(mc.get("narrative_ttl_hours", 24))
                       if n["ts"] >= cutoff and (n.get("tier") == 1 or n["weight"] >= 1.0)]

    def on_new_token(self, msg):
        pc = self.cfg.get("pumpfun", {})
        if not pc.get("watch_new_tokens", True):
            return
        for n in self.active:
            if any(nar.keyword_matches_token(k, msg.get("symbol", ""), msg.get("name", "")) >= 1.0
                   for k in n["keywords"]):
                pid = n.get("pid", "")
                self.pending.append((msg["mint"], pid))
                self.hits[pid] = self.hits.get(pid, 0) + 1
                if pc.get("early_warning", True) and n.get("tier") == 1 and self.hits[pid] <= 2:
                    self.tg.send(alerts.new_token_alert(msg, n))
                return

    def drain(self):
        while self.pending:
            mint, pid = self.pending.pop(0)
            self.store.watch(mint, pid)


async def live(cfg, store, tg, every_min: float, duration_s: float | None = None):
    li = Listener(cfg, store, tg)
    stream = asyncio.create_task(pumpportal.stream(li.on_new_token))
    poll = cfg.get("x", {}).get("poll_seconds", 60)
    last_full = 0.0
    end = time.time() + duration_s if duration_s else None
    while end is None or time.time() < end:
        li.drain()
        full = time.time() - last_full >= every_min * 60
        await asyncio.to_thread(run_once, cfg, store, tg, not full)
        if full:
            last_full = time.time()
        li.refresh()
        wait = poll if end is None else max(0.0, min(poll, end - time.time()))
        await asyncio.sleep(wait)
        if stream.done():
            stream = asyncio.create_task(pumpportal.stream(li.on_new_token))
    stream.cancel()
    li.drain()
    store.save()
    log.info("Live shift finished")


async def listen_for(cfg, store, tg, seconds: float):
    li = Listener(cfg, store, tg)
    await pumpportal.stream(li.on_new_token, seconds)
    li.drain()


def main():
    ap = argparse.ArgumentParser(description="Memecoin narrative scanner")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--listen", type=float, default=0, help="seconds to listen to new pump.fun coins after --once")
    ap.add_argument("--every", type=float, default=5, help="minutes between FULL scans in --live")
    ap.add_argument("--duration", type=float, default=0, help="seconds to run --live (0 = forever)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test-telegram", action="store_true")
    ap.add_argument("--config")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(a.config)
    s = cfg["secrets"]
    tg = Telegram(s["telegram_token"], s["telegram_chat_id"], dry_run=a.dry_run)
    if not (tg.token and tg.chat_id) and not a.dry_run:
        log.warning("Telegram not ready (token or chat id missing) — printing messages only")

    if a.test_telegram:
        ok = tg.send("✅ Memecoin scanner συνδέθηκε.\nΠηγές: "
                     f"X {x_twitter.check(s['twitterapi_key'])} · "
                     f"TikTok {'✓' if s['apify_token'] else '✗'} · "
                     f"Claude {'✓' if s['anthropic_key'] else '✗ (χωρίς εξηγήσεις AI)'} · "
                     "Ειδήσεις ✓ · DexScreener ✓ · RugCheck ✓ · pump.fun ✓\n"
                     f"Σύνοψη κάθε {cfg.get('alerts', {}).get('digest_every_minutes', 30)}'.")
        raise SystemExit(0 if ok else 1)

    store = Store()
    if a.live:
        asyncio.run(live(cfg, store, tg, a.every, a.duration or None))
    else:
        if a.listen > 0:  # listen first, so the scan right after already checks the new coins
            asyncio.run(listen_for(cfg, store, tg, a.listen))
        run_once(cfg, store, tg)


if __name__ == "__main__":
    main()
