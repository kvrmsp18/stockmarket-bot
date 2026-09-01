from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .config import settings


@dataclass
class BrokerHealth:
    connected: bool
    authenticated: bool
    message: str


class BrokerInterface:
    def health(self) -> BrokerHealth:
        raise NotImplementedError

    def funds(self) -> float | None:
        raise NotImplementedError

    def bulk_quotes(self, instruments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        raise NotImplementedError

    def history(self, security_id: str, exchange_segment: str = "NSE_EQ", interval: int = 5) -> pd.DataFrame:
        raise NotImplementedError

    def order(self, symbol: str, side: str, quantity: int, price: float, live: bool = False, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError

    def positions(self) -> list[dict[str, Any]]:
        return []

    def orders(self) -> list[dict[str, Any]]:
        return []


class PaperTradingBroker(BrokerInterface):
    def __init__(self, capital: float | None = None) -> None:
        self.capital = float(capital or settings.reference_capital)

    def health(self) -> BrokerHealth:
        return BrokerHealth(True, True, "PAPER BROKER READY")

    def funds(self) -> float | None:
        return self.capital

    def bulk_quotes(self, instruments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {}

    def history(self, security_id: str, exchange_segment: str = "NSE_EQ", interval: int = 5) -> pd.DataFrame:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    def order(self, symbol: str, side: str, quantity: int, price: float, live: bool = False, **kwargs: Any) -> dict[str, Any]:
        if live:
            raise RuntimeError("PaperTradingBroker cannot place live orders")
        return {
            "order_id": "PAPER-" + uuid.uuid4().hex[:16],
            "status": "FILLED",
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": price,
            "mode": "PAPER",
        }


class DhanBroker(BrokerInterface):
    """Dhan adapter using the configured one-month API key for authentication."""

    _rate_lock = threading.Lock()
    _last_quote_call = 0.0
    _last_data_call = 0.0
    _QUOTE_INTERVAL = 1.15
    _DATA_INTERVAL = 0.22
    _MAX_RETRIES = 5
    _RETRY_BASE_SECONDS = 2.0

    def __init__(self) -> None:
        self.client_id = settings.dhan_client_id
        # The one-month credential is stored as DHAN_API_KEY. Prefer it over
        # the legacy DHAN_ACCESS_TOKEN so the deployed Bot uses the credential
        # selected by the user. The legacy token remains a compatibility fallback.
        self.token = settings.dhan_market_data_token
        self.credential_source = settings.dhan_market_data_credential_source
        self.base = settings.dhan_base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "access-token": self.token,
            "client-id": self.client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    @classmethod
    def _throttle(cls, category: str) -> None:
        interval = cls._QUOTE_INTERVAL if category == "quote" else cls._DATA_INTERVAL
        with cls._rate_lock:
            now = time.monotonic()
            last = cls._last_quote_call if category == "quote" else cls._last_data_call
            wait = interval - (now - last)
            if wait > 0:
                time.sleep(wait)
            stamp = time.monotonic()
            if category == "quote":
                cls._last_quote_call = stamp
            else:
                cls._last_data_call = stamp

    @staticmethod
    def _response_code_and_message(response: requests.Response) -> tuple[str, str]:
        try:
            payload = response.json()
        except ValueError:
            return str(response.status_code), response.text[:500]
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict) and data:
                code, message = next(iter(data.items()))
                return str(code), str(message)
            return str(response.status_code), str(payload)[:500]
        return str(response.status_code), str(payload)[:500]

    def _auth_context(self) -> str:
        return (
            f"credential_source={self.credential_source}; "
            f"client_id_configured={'yes' if bool(self.client_id) else 'no'}; "
            f"credential_length={len(self.token)}"
        )

    def _request(self, method: str, path: str, category: str = "data", **kwargs: Any) -> Any:
        if not self.client_id or not self.token:
            raise RuntimeError(f"DHAN_AUTH_UNAVAILABLE: {self._auth_context()}")

        last_error = ""
        for attempt in range(self._MAX_RETRIES + 1):
            self._throttle(category)
            try:
                response = self.session.request(method, self.base + path, timeout=15, **kwargs)
            except requests.RequestException as exc:
                last_error = f"DHAN_NETWORK_ERROR: {exc}"
                if attempt >= self._MAX_RETRIES:
                    raise RuntimeError(last_error) from exc
                time.sleep(self._RETRY_BASE_SECONDS * (2**attempt))
                continue

            error_code, error_message = self._response_code_and_message(response)
            is_rate_limited = response.status_code == 429 or error_code == "805"
            if is_rate_limited:
                last_error = f"DHAN_HTTP_429: {{\"data\":{{\"805\":\"{error_message}\"}},\"status\":\"failed\"}}"
                if attempt >= self._MAX_RETRIES:
                    raise RuntimeError(last_error)
                time.sleep(self._RETRY_BASE_SECONDS * (2**attempt))
                continue

            if response.status_code == 401:
                raise RuntimeError(f"DHAN_HTTP_401: {response.text[:500]} [{self._auth_context()}]")

            if response.status_code >= 400:
                raise RuntimeError(f"DHAN_HTTP_{response.status_code}: {response.text[:500]}")

            try:
                payload = response.json()
            except ValueError as exc:
                raise RuntimeError("DHAN_INVALID_JSON_RESPONSE") from exc

            if isinstance(payload, dict) and payload.get("status") == "failed":
                data = payload.get("data", {})
                code = str(next(iter(data), "unknown")) if isinstance(data, dict) else "unknown"
                message = str(data.get(code, "Unknown error")) if isinstance(data, dict) else "Unknown error"
                if code == "805":
                    last_error = f"DHAN_HTTP_429: {{\"data\":{{\"805\":\"{message}\"}},\"status\":\"failed\"}}"
                    if attempt >= self._MAX_RETRIES:
                        raise RuntimeError(last_error)
                    time.sleep(self._RETRY_BASE_SECONDS * (2**attempt))
                    continue
                raise RuntimeError(f"DHAN_API_ERROR_{code}: {message}")

            return payload

        raise RuntimeError(last_error or "DHAN_REQUEST_FAILED")

    def health(self) -> BrokerHealth:
        if not self.client_id or not self.token:
            return BrokerHealth(False, False, f"Dhan credentials unavailable [{self._auth_context()}]")
        try:
            self.funds()
            return BrokerHealth(True, True, f"DHAN CONNECTED [{self._auth_context()}]")
        except Exception as exc:
            return BrokerHealth(False, False, str(exc))

    def funds(self) -> float | None:
        data = self._request("GET", "/v2/fundlimit", category="data")
        body = data.get("data", data) if isinstance(data, dict) else {}
        if not isinstance(body, dict):
            return None
        for key in ("availabelBalance", "availableBalance", "availabel_balance", "available_balance", "sodLimit"):
            if body.get(key) is not None:
                return float(body[key])
        return None

    @staticmethod
    def _security_id(value: Any) -> int:
        text = str(value).strip()
        if not text:
            raise ValueError("EMPTY_DHAN_SECURITY_ID")
        try:
            return int(text)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"INVALID_DHAN_SECURITY_ID: {text[:40]}") from exc

    def bulk_quotes(self, instruments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        groups: dict[str, list[int]] = {}
        for item in instruments:
            exchange_segment = str(item.get("exchange_segment", "NSE_EQ")).strip().upper()
            raw_id = item.get("security_id", "")
            if not str(raw_id).strip():
                continue
            try:
                security_id = self._security_id(raw_id)
            except ValueError:
                continue
            groups.setdefault(exchange_segment, []).append(security_id)

        out: dict[str, dict[str, Any]] = {}
        for exchange_segment, ids in groups.items():
            unique_ids = list(dict.fromkeys(ids))
            for start in range(0, len(unique_ids), 500):
                batch = unique_ids[start:start + 500]
                data = self._request("POST", "/v2/marketfeed/quote", category="quote", json={exchange_segment: batch})
                body = data.get("data", data) if isinstance(data, dict) else {}
                rows = body.get(exchange_segment, body) if isinstance(body, dict) else {}
                if isinstance(rows, dict):
                    out.update({str(key): value for key, value in rows.items() if isinstance(value, dict)})
        return out

    def history(self, security_id: str, exchange_segment: str = "NSE_EQ", interval: int = 5) -> pd.DataFrame:
        """Fetch today's Dhan intraday candles; fromDate/toDate are mandatory."""
        now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
        today = now_ist.date().isoformat()
        payload = {
            "securityId": str(self._security_id(security_id)),
            "exchangeSegment": str(exchange_segment).strip().upper(),
            "instrument": "EQUITY",
            "interval": str(int(interval)),
            "fromDate": today,
            "toDate": today,
        }
        data = self._request("POST", "/v2/charts/intraday", category="data", json=payload)
        body = data.get("data", data) if isinstance(data, dict) else {}
        keys = ["timestamp", "open", "high", "low", "close", "volume"]
        if not isinstance(body, dict) or not all(k in body for k in keys):
            return pd.DataFrame(columns=keys)
        return pd.DataFrame({
            "timestamp": pd.to_datetime(body["timestamp"], unit="s", utc=True),
            "open": body["open"],
            "high": body["high"],
            "low": body["low"],
            "close": body["close"],
            "volume": body["volume"],
        }).dropna(subset=["close"]).reset_index(drop=True)

    def order(self, symbol: str, side: str, quantity: int, price: float, live: bool = False, **kwargs: Any) -> dict[str, Any]:
        if not live or not settings.live_mode_requested:
            raise RuntimeError("LIVE_EXECUTION_BLOCKED")
        payload = {
            "transactionType": side.upper(),
            "exchangeSegment": kwargs.get("exchange_segment", "NSE_EQ"),
            "productType": "INTRADAY",
            "orderType": kwargs.get("order_type", "LIMIT"),
            "validity": "DAY",
            "securityId": str(self._security_id(kwargs["security_id"])),
            "quantity": int(quantity),
            "price": float(price),
            "disclosedQuantity": 0,
            "afterMarketOrder": False,
        }
        return self._request("POST", "/v2/orders", category="data", json=payload)

    def positions(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/v2/positions", category="data")
        return data.get("data", data) if isinstance(data, dict) else []

    def orders(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/v2/orders", category="data")
        return data.get("data", data) if isinstance(data, dict) else []


def load_security_map() -> dict[str, dict[str, Any]]:
    raw = settings.dhan_security_ids_json.strip()
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return {
        str(symbol).upper(): (value if isinstance(value, dict) else {"security_id": value, "exchange_segment": "NSE_EQ"})
        for symbol, value in obj.items()
    }
