"""Writes the Telegram explanation: why the coin exists, what happened, can it go viral, how early we are.

Uses Claude when ANTHROPIC_API_KEY is set; otherwise a rule-based Greek summary.
"""
import json
import logging
import re

from .http import post_json

log = logging.getLogger("scanner")

PROMPT = """Είσαι αναλυτής Solana memecoins. Ο χρήστης θέλει να ΜΠΕΙ ΝΩΡΙΣ σε νομίσματα που βγαίνουν από
ειδήσεις / tweets / απαντήσεις διασήμων ή KOLs. Με βάση τα ΔΕΔΟΜΕΝΑ παρακάτω γράψε σύντομα, στα ελληνικά,
χωρίς να επινοείς στοιχεία που δεν υπάρχουν. Αν δεν φαίνεται η αιτία, πες το.

Επέστρεψε ΜΟΝΟ JSON:
{"why": "γιατί βγήκε το νόμισμα (1 πρόταση)",
 "what": "τι ακριβώς έγινε: ποιος έγραψε/απάντησε τι, σε ποιον (1–2 προτάσεις)",
 "potential": "Υψηλό" | "Μεσαίο" | "Χαμηλό",
 "potential_why": "γιατί μπορεί/δεν μπορεί να γίνει viral: μέγεθος του λογαριασμού, engagement, αν το meme είναι
                   καθαρό, αν είναι ήδη γνωστό, πόσα αντίγραφα υπάρχουν (1–2 προτάσεις)",
 "early": "πόσο νωρίς είμαστε (ηλικία, MC, πόσο μεγάλο έχει γίνει ήδη) (1 πρόταση)",
 "risk": "ο μεγαλύτερος κίνδυνος (1 πρόταση)"}

ΔΕΔΟΜΕΝΑ:
"""


def _facts(m: dict, res: dict, narrative: dict | None, mentions: list[dict], copies: int) -> dict:
    f = {
        "token": {"symbol": m["symbol"], "name": m["name"], "age_minutes": round((m["age_h"] or 0) * 60),
                  "market_cap": round(m["mc"]), "liquidity": round(m["liq"]), "volume_1h": round(m["vol_1h"]),
                  "price_change_1h_pct": m["chg_1h"], "buys_1h": m["buys_1h"], "sells_1h": m["sells_1h"],
                  "has_dexscreener_profile": m["has_profile"], "twitter": m.get("twitter")},
        "coins_with_same_name": copies,
        "score": res["score"], "flags": res["flags"], "x_social": res.get("social"),
        "holders": res.get("holders"),
    }
    if narrative:
        f["narrative"] = {k: narrative.get(k) for k in
                          ("source", "kind", "author", "text", "context_author", "context", "tier", "why")}
    if mentions:
        f["top_x_mentions"] = [{"author": t["author"], "followers": t["followers"], "likes": t["likes"],
                                "text": t["text"][:200]} for t in mentions[:5]]
    return f


def fallback(m: dict, res: dict, narrative: dict | None, mentions: list[dict], copies: int) -> dict:
    if narrative:
        who = f"@{narrative['author']}" if narrative.get("author") else narrative["source"]
        kind = {"reply": "απάντησε", "retweet": "έκανε retweet", "quote": "έκανε quote"}.get(
            narrative.get("kind"), "έγραψε")
        ctx = f" στον @{narrative['context_author']}" if narrative.get("context_author") and narrative.get("kind") == "reply" else ""
        if narrative.get("context"):
            why = (f"Βγήκε μετά από {'απάντηση' if narrative.get('kind') == 'reply' else 'ανάρτηση'} του {who} "
                   f"σε post για: «{narrative['context'][:120]}»")
        else:
            why = f"Πάνω στο αφήγημα «{', '.join(narrative['keywords'][:3])}» από {who}."
        what = f"Ο {who} {kind}{ctx}: «{narrative['text'][:140]}»"
        pot = "Υψηλό" if narrative.get("tier") == 1 else "Μεσαίο"
        pot_why = ("Tier-1 λογαριασμός (τεράστιο κοινό)." if narrative.get("tier") == 1
                   else "Από KOL / viral post — δεν έχει γίνει ακόμα mainstream.")
    elif mentions:
        top = mentions[0]
        why = "Η αιτία δεν είναι ξεκάθαρη — δες τα tweets που το αναφέρουν."
        what = f"Το πιο δημοφιλές tweet: @{top['author']} ({top['followers']:,} followers): «{top['text'][:140]}»"
        pot = "Μεσαίο" if top["followers"] > 50000 else "Χαμηλό"
        pot_why = f"Αναφέρεται από {len(mentions)} λογαριασμούς στο X."
    else:
        why = "Δεν βρέθηκε αφήγημα — πιάστηκε από τον όγκο/ταχύτητα στο DexScreener."
        what = "Καμία αναφορά από γνωστό λογαριασμό ακόμα."
        pot, pot_why = "Χαμηλό", "Χωρίς ιστορία πίσω του είναι πιο εύκολο να πεθάνει."
    if copies > 1:
        pot_why += f" Υπάρχουν {copies} αντίγραφα με το ίδιο όνομα."
    age = (m["age_h"] or 0) * 60
    early = f"Ηλικία {age:.0f} λεπτά, MC ${m['mc']/1000:.0f}K."
    risk = res["flags"][0] if res["flags"] else "Τα memecoins κάνουν −80% πολύ γρήγορα."
    return {"why": why, "what": what, "potential": pot, "potential_why": pot_why, "early": early, "risk": risk}


def explain(m: dict, res: dict, narrative: dict | None, mentions: list[dict], copies: int,
            key: str = "", model: str = "claude-haiku-5-5") -> dict:
    base = fallback(m, res, narrative, mentions, copies)
    if not key:
        return base
    out = post_json(
        "https://api.anthropic.com/v1/messages",
        {"model": model, "max_tokens": 1200, "messages": [
            {"role": "user", "content": PROMPT + json.dumps(_facts(m, res, narrative, mentions, copies),
                                                            ensure_ascii=False)}]},
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        timeout=60,
    )
    if not out:
        return base
    text = "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")
    mm = re.search(r"\{.*\}", re.sub(r"```(?:json)?", "", text), re.S)
    if not mm:
        log.warning("Claude explanation not parseable: %s", text[:200].replace("\n", " "))
        return base
    try:
        data = json.loads(mm.group(0))
    except ValueError:
        return base
    return {k: str(data.get(k) or base[k]) for k in base}
