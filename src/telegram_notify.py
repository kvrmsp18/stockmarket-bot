"""Optional Telegram notifications for broker health and trading events."""

from __future__ import annotations

import os

import requests


class TelegramNotificationError(RuntimeError):
    """Raised when a configured Telegram notification cannot be delivered."""


class TelegramNotifier:
    """Small Telegram Bot API client.

    If TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing, notifications are
    simply disabled. No Telegram secret is ever logged or returned.
    """

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    @staticmethod
    def _telegram_detail(response: requests.Response) -> str:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                description = payload.get("description") or payload.get("error")
                if description:
                    return str(description)[:500]
        except ValueError:
            pass
        return response.text.strip()[:500] or "No response description"

    def send(self, message: str) -> bool:
        if not self.configured:
            return False
        if not message.strip():
            raise ValueError("Telegram message cannot be empty.")

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            response = requests.post(
                url,
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                    "disable_web_page_preview": True,
                },
                timeout=self.timeout,
            )
            if not response.ok:
                detail = self._telegram_detail(response)
                raise TelegramNotificationError(
                    f"Telegram returned HTTP {response.status_code}: {detail}"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise TelegramNotificationError("Telegram returned a non-JSON response.") from exc
            if not payload.get("ok", False):
                detail = payload.get("description") or "Telegram rejected the message."
                raise TelegramNotificationError(str(detail)[:500])
            return True
        except requests.RequestException as exc:
            raise TelegramNotificationError(
                f"Telegram network error: {exc}"
            ) from exc

    def insufficient_funds(
        self,
        available_funds: float,
        *,
        required_amount: float | None = None,
    ) -> bool:
        required_text = (
            f"\nRequired for candidate: ₹{required_amount:,.2f}"
            if required_amount is not None
            else ""
        )
        return self.send(
            "🔴 Dhan funds alert\n"
            f"Available funds: ₹{available_funds:,.2f}"
            f"{required_text}\n"
            "Research and P&L estimation will continue. Real BUY/SELL execution is blocked."
        )

    def authentication_problem(self, detail: str) -> bool:
        return self.send(
            "🔐 Dhan authentication alert\n"
            "The Bot could not authenticate/renew the Dhan access token.\n"
            f"Detail: {detail}\n"
            "Research can continue, but broker balance and real execution are unavailable until authentication recovers."
        )

    def order_event(self, message: str) -> bool:
        return self.send(message)
