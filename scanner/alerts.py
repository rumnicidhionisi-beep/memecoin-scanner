from datetime import datetime
from zoneinfo import ZoneInfo

from .telegram import esc

POT_ICON = {"Υψηλό": "🟢", "Μεσαίο": "🟡", "Χαμηλό": "🔴"}


def _money(v) -> str:
    v = float(v or 0)
    if v >= 1e6:
        return f"${v/1e6:.2f}M"
    if v >= 1e3:
        return f"${v/1e3:.0f}K"
    return f"${v:.0f}"


def _age(h) -> str:
    if h is None:
        return "?"
    return f"{h*60:.0f} λεπτά" if h < 1 else f"{h:.1f} ώρες"


def _links(mint: str, url: str = "") -> str:
    url = url or f"https://dexscreener.com/solana/{mint}"
    return (f'<a href="{esc(url)}">DexScreener</a> · '
            f'<a href="https://rugcheck.xyz/tokens/{esc(mint)}">RugCheck</a> · '
            f'<a href="https://app.bubblemaps.io/sol/token/{esc(mint)}">Bubblemaps</a> · '
            f'<a href="https://x.com/search?q={esc(mint)}&amp;f=live">X</a>')


def token_alert(m: dict, res: dict, cfg: dict, narrative: dict | None, ex: dict, lane: str = "") -> str:
    hot = res["score"] >= cfg.get("alerts", {}).get("hot_score", 80)
    head = "🔥 ΖΕΣΤΟ" if hot else "🟢 ΣΗΜΑ"
    tag = {"narrative": "πάνω σε αφήγημα", "early": "γρήγορο νόμισμα", "survivors": "48h survivor",
           "x_mint": "contract σε tweet", "pumpfun": "νέο pump.fun"}.get(lane, "")
    lines = [
        f"{head} <b>${esc(m['symbol'])}</b> — {esc(m['name'])}{'  · ' + tag if tag else ''}",
        f"Σκορ <b>{res['score']}/100</b> · MC {_money(m['mc'])} · Ηλικία {_age(m['age_h'])}",
        f"Liq {_money(m['liq'])} · Vol1h {_money(m['vol_1h'])} · 1h {m['chg_1h']:+.0f}% · "
        f"B/S {m['buys_1h']}/{m['sells_1h']}",
        "",
        f"❓ <b>Γιατί βγήκε:</b> {esc(ex['why'])}",
        f"📰 <b>Τι έγινε:</b> {esc(ex['what'])}",
        f"{POT_ICON.get(ex['potential'], '🟡')} <b>Περιθώριο να γίνει viral: {esc(ex['potential'])}</b> — "
        f"{esc(ex['potential_why'])}",
        f"⏱️ <b>Πόσο νωρίς:</b> {esc(ex['early'])}",
        f"⚠️ <b>Κίνδυνος:</b> {esc(ex['risk'])}",
    ]
    h = res.get("holders") or {}
    if h.get("top10_pct") is not None or h.get("dev_pct") is not None:
        def _p(v):
            return "?" if v is None else f"{v:.1f}%"
        lines.append(f"👥 <b>Holders:</b> Top10 {_p(h.get('top10_pct'))} · Dev {_p(h.get('dev_pct'))} · "
                     f"Insiders {_p(h.get('insiders_pct'))}"
                     f"{' (' + str(h.get('insiders_count')) + ')' if h.get('insiders_count') else ''}")
    if narrative and narrative.get("url"):
        lines.append(f'🔗 <a href="{esc(narrative["url"])}">Η ανάρτηση</a>')
    extra = [f for f in res["flags"] if f != ex["risk"]][:3]
    if extra:
        lines.append("")
        lines += [f"• {esc(f)}" for f in extra]
    lines += [
        "",
        f"<code>{esc(m['mint'])}</code>",
        _links(m["mint"], m.get("url", "")),
        "Bundles/snipers δεν φαίνονται δωρεάν → γρήγορη ματιά στο Padre (Bundles ≤5%, Snipers ≤10%) πριν μπεις.",
    ]
    return "\n".join(lines)


def narrative_alert(n: dict) -> str:
    src = {"x": "𝕏", "tiktok": "🎵 TikTok", "news": "📰"}.get(n["source"], n["source"])
    kind = {"reply": " απάντησε", "retweet": " έκανε retweet", "quote": " έκανε quote"}.get(n.get("kind"), "")
    lines = [f"📣 <b>Νέο αφήγημα</b> — {src} @{esc(n.get('author',''))}{kind}",
             f"Πιθανό όνομα νομίσματος: <b>{esc(', '.join(n['keywords']))}</b>"]
    if n.get("why"):
        lines.append(f"💬 {esc(n['why'])}")
    lines.append(f"<i>«{esc(n['text'][:220])}»</i>")
    if n.get("context"):
        lines.append(f"↪️ σε @{esc(n.get('context_author',''))}: <i>«{esc(n['context'][:180])}»</i>")
    if n.get("url"):
        lines.append(f'<a href="{esc(n["url"])}">Άνοιγμα</a>')
    lines.append("Δεν υπάρχει ακόμα token με κίνηση. Ψάχνω σε κάθε κύκλο, θα σου στείλω μόλις εμφανιστεί.")
    return "\n".join(lines)


def new_token_alert(msg: dict, n: dict) -> str:
    mint = msg.get("mint", "")
    return "\n".join([
        f"⚡ <b>Μόλις φτιάχτηκε στο pump.fun</b>: <b>${esc(msg.get('symbol',''))}</b> — {esc(msg.get('name',''))}",
        f"Αφήγημα: @{esc(n.get('author',''))} → {esc(', '.join(n['keywords']))}",
        f"<code>{esc(mint)}</code>",
        f'<a href="https://pump.fun/coin/{esc(mint)}">pump.fun</a> · '
        f'<a href="https://dexscreener.com/solana/{esc(mint)}">DexScreener</a>',
        "⚠️ Πρώτα λεπτά: συνήθως βγαίνουν πολλά αντίγραφα. Το παρακολουθώ και θα σου στείλω "
        "πλήρη ανάλυση αν πιάσει κίνηση.",
    ])


def digest(events: list[dict], minutes: int, tz: str = "Europe/Athens") -> str:
    coins = [e for e in events if e["type"] == "coin"]
    narrs = [e for e in events if e["type"] == "narrative"]
    news = [e for e in events if e["type"] == "news"]
    lines = [f"🗞️ <b>Σύνοψη τελευταίων {minutes}'</b> · {datetime.now(ZoneInfo(tz)).strftime('%H:%M')}"]
    if coins:
        lines.append("\n<b>Νομίσματα</b>")
        for e in sorted(coins, key=lambda e: e["score"], reverse=True)[:8]:
            lines.append(f"• <b>${esc(e['symbol'])}</b> {e['score']}/100 · MC {_money(e['mc'])} · "
                         f"{esc(e['why'][:90])}\n  <code>{esc(e['mint'])}</code>")
    if narrs:
        lines.append("\n<b>Αφηγήματα (X / TikTok)</b>")
        for e in sorted(narrs, key=lambda e: e["weight"], reverse=True)[:8]:
            lines.append(f"• @{esc(e['author'])}: {esc(', '.join(e['keywords'][:3]))}"
                         f"{' — ' + esc(e['why']) if e.get('why') else ''}")
    if news:
        lines.append("\n<b>Ειδήσεις</b>")
        for e in news[:6]:
            lines.append(f'• <a href="{esc(e["url"])}">{esc(e["text"][:110])}</a>')
    if len(lines) == 1:
        lines.append("Τίποτα σημαντικό σε αυτό το διάστημα.")
    return "\n".join(lines)
