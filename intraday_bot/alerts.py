from __future__ import annotations
import os
import requests


def telegram(message:str)->bool:
    token=os.getenv("TELEGRAM_BOT_TOKEN","").strip(); chat=os.getenv("TELEGRAM_CHAT_ID","").strip()
    if not token or not chat:return False
    try:
        r=requests.post(f"https://api.telegram.org/bot{token}/sendMessage",json={"chat_id":chat,"text":message,"disable_web_page_preview":True},timeout=10);return r.ok
    except Exception:return False


def critical(kind:str,detail:str)->bool:
    return telegram(f"🚨 STOCKMARKET BOT\n{kind}\n{detail}")
