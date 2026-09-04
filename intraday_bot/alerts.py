from __future__ import annotations

import requests

from .config import settings


def telegram(message: str) -> bool:
    """Send a Telegram message using the configured GitHub/Streamlit secrets."""
    token = settings.telegram_token.strip()
    chat = settings.telegram_chat_id.strip()
    if not token or not chat:
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": message, "disable_web_page_preview": True},
            timeout=10,
        )
        if not response.ok:
            return False
        payload = response.json()
        return bool(isinstance(payload, dict) and payload.get("ok") is True)
    except (requests.RequestException, ValueError):
        return False


def critical(kind: str, detail: str) -> bool:
    return telegram(f"🚨 STOCKMARKET BOT\n{kind}\n{detail}")
