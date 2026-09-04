from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
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
    def health(self) -> BrokerHealth: raise NotImplementedError
    def funds(self) -> float | None: raise NotImplementedError
    def bulk_quotes(self, instruments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]: raise NotImplementedError
    def history(self, security_id: str, exchange_segment: str = "NSE_EQ", interval: int = 5, instrument: str = "EQUITY") -> pd.DataFrame: raise NotImplementedError
    def daily_history(self, security_id: str, exchange_segment: str = "NSE_EQ", instrument: str = "EQUITY") -> pd.DataFrame: raise NotImplementedError
    def order(self, symbol: str, side: str, quantity: int, price: float, live: bool = False, **kwargs: Any) -> dict[str, Any]: raise NotImplementedError
    def positions(self) -> list[dict[str, Any]]: return []
    def orders(self) -> list[dict[str, Any]]: return []


class PaperTradingBroker(BrokerInterface):
    def __init__(self, capital: float | None = None) -> None:
        self.capital = float(capital or settings.reference_capital)

    def health(self) -> BrokerHealth:
        return BrokerHealth(True, True, "PAPER BROKER READY")

    def funds(self) -> float | None:
        return self.capital

    def bulk_quotes(self, instruments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {}

    def history(self, security_id: str, exchange_segment: str = "NSE_EQ", interval: int = 5, instrument: str = "EQUITY") -> pd.DataFrame:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    def daily_history(self, security_id: str, exchange_segment: str = "NSE_EQ", instrument: str = "EQUITY") -> pd.DataFrame:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    def order(self, symbol: str, side: str, quantity: int, price: float, live: bool = False, **kwargs: Any) -> dict[str, Any]:
        if live:
            raise RuntimeError("PaperTradingBroker cannot place live orders")
        return {"order_id": "PAPER-" + uuid.uuid4().hex[:16], "status": "FILLED", "symbol": symbol, "side": side, "quantity": quantity, "price": price, "mode": "PAPER"}


class DhanBroker(BrokerInterface):
    """Dhan read/data adapter using the single manually entered DHAN_API_KEY credential."""

    _rate_lock = threading.Lock()
    _last_quote_call = 0.0
    _last_data_call = 0.0
    _QUOTE_INTERVAL = 1.15
    _DATA_INTERVAL = 0.22
    _MAX_RETRIES = 5
    _RETRY_BASE_SECONDS = 2.0

    def __init__(self) -> None:
        self.client_id = settings.dhan_client_id
        self.token = settings.dhan_market_data_token
        self.base = settings.dhan_base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "access-token": self.token,
            "client-id": self.client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def _auth_context(self) -> str:
        return (
            f"credential_source={settings.dhan_market_data_credential_source}; "
            f"client_id_configured={'yes' if self.client_id else 'no'}; "
            f"manual_credential_configured={'yes' if self.token else 'no'}; "
            f"manual_credential_length={len(self.token)}"
        )

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
                time.sleep(self._RETRY_BASE_SECONDS * (2 ** attempt))
                continue
            code, message = self._response_code_and_message(response)
            if response.status_code == 429 or code == "805":
                last_error = f"DHAN_HTTP_429: {message}"
                if attempt >= self._MAX_RETRIES:
                    raise RuntimeError(last_error)
                time.sleep(self._RETRY_BASE_SECONDS * (2 ** attempt))
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
                err_code = str(next(iter(data), "unknown")) if isinstance(data, dict) else "unknown"
                err_message = str(data.get(err_code, "Unknown error")) if isinstance(data, dict) else "Unknown error"
                if err_code == "805":
                    last_error = f"DHAN_HTTP_429: {err_message}"
                    if attempt >= self._MAX_RETRIES:
                        raise RuntimeError(last_error)
                    time.sleep(self._RETRY_BASE_SECONDS * (2 ** attempt))
                    continue
                raise RuntimeError(f"DHAN_API_ERROR_{err_code}: {err_message}")
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
        data = self._request("GET", "/v2/fundlimit")
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

    @staticmethod
    def _quote_rows(payload: Any, exchange_segment: str) -> dict[str, dict[str, Any]]:
        body = payload.get("data", payload) if isinstance(payload, dict) else {}
        if not isinstance(body, dict):
            return {}
        rows = body.get(exchange_segment, body)
        if not isinstance(rows, dict):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for key, value in rows.items():
            if isinstance(value, dict):
                out[str(key)] = value
            elif isinstance(value, (int, float)):
                out[str(key)] = {"last_price": float(value), "ltp": float(value)}
        return out

    def _ltp_batch(self, exchange_segment: str, batch: list[int]) -> dict[str, dict[str, Any]]:
        payload = {exchange_segment: batch}
        return self._quote_rows(self._request("POST", "/v2/marketfeed/ltp", category="quote", json=payload), exchange_segment)

    def bulk_quotes(self, instruments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        groups: dict[str, list[int]] = {}
        for item in instruments:
            segment = str(item.get("exchange_segment", "NSE_EQ")).strip().upper()
            try:
                sid = self._security_id(item.get("security_id", ""))
            except ValueError:
                continue
            groups.setdefault(segment, []).append(sid)
        out: dict[str, dict[str, Any]] = {}
        for segment, ids in groups.items():
            ids = list(dict.fromkeys(ids))
            for start in range(0, len(ids), 500):
                batch = ids[start:start + 500]
                try:
                    rows = self._quote_rows(self._request("POST", "/v2/marketfeed/quote", category="quote", json={segment: batch}), segment)
                    if not rows:
                        rows = self._ltp_batch(segment, batch)
                except RuntimeError as quote_error:
                    try:
                        rows = self._ltp_batch(segment, batch)
                    except Exception as ltp_error:
                        raise RuntimeError(f"DHAN_MARKETFEED_FAILED: quote={quote_error}; ltp={ltp_error}") from ltp_error
                    if not rows:
                        raise RuntimeError(f"DHAN_MARKETFEED_EMPTY: quote={quote_error}; ltp returned zero rows")
                out.update(rows)
        return out

    @staticmethod
    def _history_start_date(today: datetime) -> str:
        cursor = today.date() - timedelta(days=7)
        while cursor.weekday() >= 5:
            cursor -= timedelta(days=1)
        return cursor.isoformat()

    @staticmethod
    def _daily_start_date(today: datetime) -> str:
        return (today.date() - timedelta(days=420)).isoformat()

    @staticmethod
    def _frame(body: Any) -> pd.DataFrame:
        keys = ["timestamp", "open", "high", "low", "close", "volume"]
        if not isinstance(body, dict) or not all(k in body for k in keys):
            return pd.DataFrame(columns=keys)
        try:
            return pd.DataFrame({
                "timestamp": pd.to_datetime(body["timestamp"], unit="s", utc=True),
                "open": body["open"], "high": body["high"], "low": body["low"],
                "close": body["close"], "volume": body["volume"],
            }).dropna(subset=["close"]).reset_index(drop=True)
        except (TypeError, ValueError, KeyError):
            return pd.DataFrame(columns=keys)

    def history(self, security_id: str, exchange_segment: str = "NSE_EQ", interval: int = 5, instrument: str = "EQUITY") -> pd.DataFrame:
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
        payload = {
            "securityId": str(self._security_id(security_id)),
            "exchangeSegment": str(exchange_segment).strip().upper(),
            "instrument": str(instrument).strip().upper(),
            "interval": str(int(interval)),
            "fromDate": self._history_start_date(now),
            "toDate": now.date().isoformat(),
        }
        data = self._request("POST", "/v2/charts/intraday", json=payload)
        return self._frame(data.get("data", data) if isinstance(data, dict) else {})

    def daily_history(self, security_id: str, exchange_segment: str = "NSE_EQ", instrument: str = "EQUITY") -> pd.DataFrame:
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
        payload = {
            "securityId": str(self._security_id(security_id)),
            "exchangeSegment": str(exchange_segment).strip().upper(),
            "instrument": str(instrument).strip().upper(),
            "expiryCode": 0,
            "oi": False,
            "fromDate": self._daily_start_date(now),
            "toDate": now.date().isoformat(),
        }
        data = self._request("POST", "/v2/charts/historical", json=payload)
        return self._frame(data.get("data", data) if isinstance(data, dict) else {})

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
        return self._request("POST", "/v2/orders", json=payload)

    def positions(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/v2/positions")
        return data.get("data", data) if isinstance(data, dict) else []

    def orders(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/v2/orders")
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
