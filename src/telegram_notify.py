"""Telegram notifications with safe chat-id recovery and diagnostics."""

from __future__ import annotations

import os

import requests


class TelegramNotificationError(RuntimeError):
    """Raised when a configured Telegram notification cannot be delivered."""


class TelegramNotifier:
    """Small Telegram Bot API client.

    If TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing, notifications are
    disabled. When the configured chat ID is rejected as "chat not found", the
    notifier can discover a unique private/group chat that has interacted with
    the bot and retry once. It never logs the bot token.
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
        self.auto_discover = os.getenv("TELEGRAM_AUTO_DISCOVER_CHAT_ID", "true").strip().lower() == "true"

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

    def _send_once(self, message: str, chat_id: str) -> requests.Response:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        return requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=self.timeout,
        )

    def _discover_unique_chat_id(self) -> str | None:
        """Discover a single chat that has interacted with the bot.

        Only a unique chat ID is accepted; multiple chats are never guessed.
        This requires the bot to have received a message (for example /start)
        and requires Telegram polling to be available.
        """
        if not self.auto_discover or not self.bot_token:
            return None
        try:
            response = requests.get(
                f"https://api.telegram.org/bot{self.bot_token}/getUpdates",
                params={"limit": 100, "allowed_updates": '["message","my_chat_member"]'},
                timeout=self.timeout,
            )
            if not response.ok:
                return None
            payload = response.json()
            if not isinstance(payload, dict) or not payload.get("ok"):
                return None

            chat_ids: set[str] = set()
            for update in payload.get("result", []):
                if not isinstance(update, dict):
                    continue
                message = update.get("message")
                if isinstance(message, dict) and isinstance(message.get("chat"), dict):
                    value = message["chat"].get("id")
                    if value is not None:
                        chat_ids.add(str(value))
                member = update.get("my_chat_member")
                if isinstance(member, dict) and isinstance(member.get("chat"), dict):
                    value = member["chat"].get("id")
                    if value is not None:
                        chat_ids.add(str(value))

            if len(chat_ids) == 1:
                return next(iter(chat_ids))
        except (requests.RequestException, ValueError, TypeError):
            return None
        return None

    def send(self, message: str) -> bool:
        if not self.configured:
            return False
        if not message.strip():
            raise ValueError("Telegram message cannot be empty.")

        try:
            response = self._send_once(message, self.chat_id)
        except requests.RequestException as exc:
            raise TelegramNotificationError(f"Telegram network error: {exc}") from exc

        if response.ok:
            try:
                payload = response.json()
            except ValueError as exc:
                raise TelegramNotificationError("Telegram returned a non-JSON response.") from exc
            if payload.get("ok", False):
                return True

        detail = self._telegram_detail(response)
        if response.status_code == 400 and detail.lower() == "bad request: chat not found":
            discovered = self._discover_unique_chat_id()
            if discovered and discovered != self.chat_id:
                try:
                    retry = self._send_once(message, discovered)
                except requests.RequestException as exc:
                    raise TelegramNotificationError(f"Telegram network error after chat-id recovery: {exc}") from exc
                if retry.ok:
                    try:
                        retry_payload = retry.json()
                    except ValueError as exc:
                        raise TelegramNotificationError("Telegram returned a non-JSON response after chat-id recovery.") from exc
                    if retry_payload.get("ok", False):
                        self.chat_id = discovered
                        return True
                retry_detail = self._telegram_detail(retry)
                raise TelegramNotificationError(
                    f"Telegram chat-id recovery failed with HTTP {retry.status_code}: {retry_detail}"
                )
            raise TelegramNotificationError(
                "Telegram returned HTTP 400: Bad Request: chat not found. "
                "No unique chat could be auto-discovered; verify TELEGRAM_CHAT_ID or send /start to the bot."
            )

        raise TelegramNotificationError(f"Telegram returned HTTP {response.status_code}: {detail}")

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
