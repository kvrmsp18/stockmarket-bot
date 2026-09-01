from __future__ import annotations

import os

import requests

from .config import settings


def telegram(message: str) -> bool:
    """Deliver a Telegram alert using environment or Streamlit-secret configuration."""
    token = settings.telegram_token.strip() or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = settings.telegram_chat_id.strip() or os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": message, "disable_web_page_preview": True},
            timeout=10,
        )
        return response.ok
    except Exception:
        return False


def critical(kind: str, detail: str) -> bool:
    return telegram(f"🚨 STOCKMARKET BOT\n{kind}\n{detail}")
