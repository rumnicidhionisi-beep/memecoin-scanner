import html
import logging

from .http import get_json, post_json

log = logging.getLogger("scanner")


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=False)


def discover_chat_id(token: str) -> str:
    """If TELEGRAM_CHAT_ID is not set: use the last private chat that wrote to the bot (/start)."""
    data = get_json(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15) or {}
    for upd in reversed(data.get("result") or []):
        msg = upd.get("message") or upd.get("my_chat_member") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            return str(chat["id"])
    return ""


class Telegram:
    def __init__(self, token: str, chat_id: str, dry_run: bool = False):
        self.token, self.chat_id, self.dry_run = token, chat_id, dry_run
        self.sent = []  # kept for tests / summary
        if token and not chat_id and not dry_run:
            self.chat_id = discover_chat_id(token)
            if self.chat_id:
                log.info("Telegram chat id found automatically: %s", self.chat_id)
            else:
                log.warning("No TELEGRAM_CHAT_ID and nobody wrote to the bot yet — send /start to your bot")

    def send(self, text: str) -> bool:
        self.sent.append(text)
        if self.dry_run or not (self.token and self.chat_id):
            print("\n----- TELEGRAM (dry-run) -----\n" + text + "\n------------------------------")
            return True
        res = post_json(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            {"chat_id": self.chat_id, "text": text[:4000], "parse_mode": "HTML",
             "disable_web_page_preview": True},
            timeout=20,
        )
        ok = bool(res and res.get("ok"))
        if not ok:
            log.warning("Telegram send failed: %s", res)
        return ok
