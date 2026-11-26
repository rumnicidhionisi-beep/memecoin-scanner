import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no extra dependency). Existing env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, val = s.split("=", 1)
        val = val.split(" #", 1)[0]
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and val and key not in os.environ:
            os.environ[key] = val


def load_config(config_path: str | None = None) -> dict:
    _load_dotenv(ROOT / ".env")
    cfg_file = Path(config_path) if config_path else ROOT / "config.yaml"
    cfg = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}

    cfg["secrets"] = {
        "telegram_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
        "twitterapi_key": os.getenv("TWITTERAPI_IO_KEY", ""),
        "apify_token": os.getenv("APIFY_TOKEN", ""),
        "anthropic_key": os.getenv("ANTHROPIC_API_KEY", ""),
        "anthropic_model": os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5"),
    }
    return cfg
