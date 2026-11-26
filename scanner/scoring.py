"""Filters + 0–100 score. Rules from the Nov 2026 paper tests and filter work, tuned for SPEED."""
import re


def _lane_check(m: dict, lane: dict) -> str | None:
    age = m["age_h"]
    if age is None:
        return "άγνωστη ηλικία"
    if age < lane.get("min_age_hours", 0):
        return "πολύ νέο"
    if age > lane.get("max_age_hours", 48):
        return "παλιό"
    if m["mc"] < lane.get("min_market_cap", 0):
        return "MC χαμηλό"
    if m["liq"] < lane.get("min_liquidity_usd", 0):
        return "χαμηλή ρευστότητα"
    if m["vol_1h"] < lane.get("min_volume_1h_usd", 0):
        return "χαμηλός όγκος 1h"
    if m["tx_1h"] and m["avg_trade_1h"] < lane.get("min_avg_trade_usd", 0):
        return "micro-bots (μικρές συναλλαγές)"
    if m["chg_1h"] < lane.get("min_price_change_1h", -999):
        return "δεν ανεβαίνει"
    if m["buys_1h"] < m["sells_1h"] * 0.8:
        return "πωλητές > αγοραστές"
    return None


def onchain_lane(m: dict, oc: dict) -> str | None:
    """Return the lane name ('early' / 'survivors') the coin passes, or None."""
    for name in ("early", "survivors"):
        lane = oc.get(name) or {}
        if lane.get("enabled", True) and _lane_check(m, lane) is None:
            return name
    return None


def narrative_filter(m: dict, mc: dict) -> str | None:
    if m["age_h"] is not None and m["age_h"] > mc.get("max_token_age_hours", 48):
        return "παλιό"
    if m["mc"] < mc.get("min_market_cap", 10000):
        return "MC χαμηλό"
    if m["liq"] < mc.get("min_liquidity_usd", 4000):
        return "χαμηλή ρευστότητα"
    if m["vol_1h"] < mc.get("min_volume_1h_usd", 2000):
        return "χαμηλός όγκος"
    return None


def x_handle(url: str) -> str | None:
    """x.com/handle -> handle. Communities / status links return None."""
    m = re.match(r"https?://(?:www\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/?$", url or "")
    if not m or m.group(1).lower() in {"i", "home", "search", "intent"}:
        return None
    return m.group(1)


def score(m: dict, rug: dict | None, cfg: dict, narrative: dict | None = None,
          match: float = 0.0, x_age_days: float | None = None, copies: int = 0) -> dict:
    sc = cfg.get("safety", {})
    oc = cfg.get("onchain", {})
    reasons, flags = [], []
    rejected = None

    # ---------- safety (max 20) ----------
    safety = 0
    if rug is None:
        safety = 6
        flags.append("RugCheck μη διαθέσιμο — έλεγξε στο Padre")
    else:
        rs = rug.get("score")
        if rug.get("rugged"):
            rejected = "RUGGED"
        if sc.get("reject_if_mint_authority", True) and rug.get("mint_authority"):
            rejected = rejected or "Mint authority ενεργό"
        if sc.get("reject_if_freeze_authority", True) and rug.get("freeze_authority"):
            rejected = rejected or "Freeze authority ενεργό"
        if rs is not None and rs > sc.get("max_rugcheck_score", 60):
            rejected = rejected or f"RugCheck {rs}/100"
        if rs is not None:
            safety = 20 if rs <= 20 else 12 if rs <= 40 else 6
            reasons.append(f"RugCheck {rs}/100")
        # --- the Padre checks: top10 / dev / insiders ---
        t10, dev, ins = rug.get("top10_pct"), rug.get("dev_pct"), rug.get("insiders_pct")
        if t10 is not None and t10 > sc.get("max_top10_pct", 25):
            safety -= 8
            flags.append(f"Top10 κατέχουν {t10:.0f}% (όριο {sc.get('max_top10_pct', 25)}%)")
        if dev is not None and dev > sc.get("max_dev_pct", 5):
            safety -= 6
            flags.append(f"Ο dev κρατάει {dev:.1f}%")
        if ins is not None and ins > sc.get("reject_insiders_pct", 40):
            rejected = rejected or f"Insiders {ins:.0f}%"
        elif ins is not None and ins > sc.get("max_insiders_pct", 20):
            safety -= 8
            flags.append(f"Insiders κρατάνε {ins:.0f}% ({rug.get('insiders_count')} πορτοφόλια)")
        if rug.get("full") and t10 is not None and t10 <= sc.get("max_top10_pct", 25) \
                and (dev is None or dev <= sc.get("max_dev_pct", 5)) and (ins or 0) <= sc.get("max_insiders_pct", 20):
            safety += 3
            reasons.append(f"Holders καθαροί: Top10 {t10:.0f}% · Dev {dev or 0:.1f}% · Insiders {ins or 0:.0f}%")
        for d in rug.get("danger", [])[:3]:
            flags.append(f"⚠️ {d}")
    if x_age_days is not None and x_age_days < sc.get("min_x_account_age_days", 7):
        safety -= 8
        flags.append(f"Λογαριασμός X μόλις {x_age_days:.0f} ημερών (σημάδι rug)")

    safety = min(20, safety)

    # ---------- market (max 30) ----------
    market = 0
    market += 7 if m["liq"] >= 50000 else 5 if m["liq"] >= 15000 else 2 if m["liq"] >= 4000 else 0
    market += 7 if m["vol_1h"] >= 100000 else 5 if m["vol_1h"] >= 20000 else 2 if m["vol_1h"] >= 2000 else 0
    if m["avg_trade_1h"] >= 50:
        market += 5
    elif m["avg_trade_1h"] >= 15:
        market += 3
    elif m["tx_1h"] > 50:
        market -= 5 if m["avg_trade_1h"] < 8 else 0
        flags.append(f"Μέσο trade ${m['avg_trade_1h']:.0f} — πιθανά bots")
    if m["buys_1h"] > m["sells_1h"] and m["chg_1h"] > 0:
        market += 5
        reasons.append(f"Αγοραστές>πωλητές 1h ({m['buys_1h']}/{m['sells_1h']}), τιμή {m['chg_1h']:+.0f}%")
    elif m["buys_1h"] > 1.5 * max(1, m["sells_1h"]) and m["chg_1h"] < 0:
        market -= 6
        flags.append("Πολλοί «αγοραστές» αλλά η τιμή πέφτει — παγίδα/bots")
    if m["chg_m5"] > 0 and m["buys_m5"] >= m["sells_m5"]:
        market += 3
    if m["vol_24h"] and m["vol_1h"] * 24 < 0.2 * m["vol_24h"] and (m["age_h"] or 0) > 3:
        flags.append("Ο όγκος σβήνει")
    if m["vol_liq_24h"] > oc.get("max_vol_liq_ratio_24h", 30):
        market -= 10
        flags.append(f"Όγκος/ρευστότητα {m['vol_liq_24h']:.0f}x — πιθανό fake volume")
    if m["has_profile"]:
        market += 3
    market = max(-15, min(30, market))

    # ---------- speed / how early (max 20) ----------
    speed = 0
    age = m["age_h"]
    if age is not None:
        if age < 0.5 and m["mc"] >= 50000:
            speed = 20
            reasons.insert(0, f"⚡ ${m['mc']/1000:.0f}K MC σε {age*60:.0f} λεπτά")
        elif age < 1 and m["mc"] >= 50000:
            speed = 15
            reasons.insert(0, f"⚡ ${m['mc']/1000:.0f}K MC σε {age*60:.0f} λεπτά")
        elif age < 2:
            speed = 10
        elif age < 6:
            speed = 5
        if age < 1:
            flags.append("Κάτω από 1 ώρα — ώρα των snipers/bundles, έλεγξε Padre")
        if m["mc"] >= 5_000_000:
            speed = max(0, speed - 10)
            flags.append("Ήδη πάνω από $5M — μάλλον όχι νωρίς")

    # ---------- narrative (max 30) ----------
    narr = 0
    if narrative and match > 0:
        narr = min(30, round(narrative.get("weight", 1.0) * match * 15))
        if narrative.get("tier") == 1:
            narr = min(30, narr + 8)
        who = f"@{narrative['author']}" if narrative.get("author") else narrative["source"]
        kind = {"reply": "απάντηση", "retweet": "retweet", "quote": "quote"}.get(narrative.get("kind"), "")
        reasons.insert(0, f"Αφήγημα: {who}{' (' + kind + ')' if kind else ''} → «{', '.join(narrative['keywords'][:3])}»")

    flags = [f for f in flags if f]
    total = max(0, min(100, safety + market + speed + narr))
    holders = {k: rug.get(k) for k in ("top10_pct", "dev_pct", "insiders_pct", "insiders_count")} if rug else {}
    return {"score": total, "rejected": rejected, "reasons": reasons, "flags": flags, "holders": holders,
            "parts": {"safety": safety, "market": market, "speed": speed, "narrative": narr}}
