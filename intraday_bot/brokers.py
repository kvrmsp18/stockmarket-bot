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
from .dhan_auth import clear_cached_token, get_access_token


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
    """Dhan adapter for the configured market-data credential."""

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
        self.credential_source = settings.dhan_market_data_credential_source
        self.base = settings.dhan_base_url.rstrip("/")
        self.session = requests.Session()
        self._refresh_attempted = False
        self._set_headers()

    def _set_headers(self) -> None:
        headers = {
            "access-token": self.token,
            "client-id": self.client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.credential_source == "DHAN_API_KEY":
            headers["api-key"] = self.token
        self.session.headers.update(headers)

    def _refresh_access_token_after_401(self) -> bool:
        """Recover once from a bad generated token without looping indefinitely."""
        if self._refresh_attempted:
            return False
        self._refresh_attempted = True

        # An explicitly supplied access token is user-managed; never replace it.
        if settings.dhan_access_token:
            return False
        if not (settings.dhan_client_id and settings.dhan_pin and settings.dhan_totp_secret):
            return False

        try:
            clear_cached_token()
            token, source = get_access_token(
                settings.dhan_client_id,
                settings.dhan_pin,
                settings.dhan_totp_secret,
            )
            if not token:
                return False
            self.token = token
            self.credential_source = source
            self._set_headers()
            return True
        except Exception as exc:
            raise RuntimeError(f"DHAN_TOKEN_REFRESH_FAILED: {exc}") from exc

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

        refresh_attempts = 0
        last_error = ""
        for attempt in range(self._MAX_RETRIES + 1):
            self._throttle(category)
            try:
                response = self.session.request(method, self.base + path, timeout=15, **kwargs)
            except requests.RequestException as exc:
                last_error = str(exc)
                if attempt >= self._MAX_RETRIES:
                    raise RuntimeError(f"DHAN_NETWORK_ERROR: {last_error}") from exc
                time.sleep(self._RETRY_BASE_SECONDS * (2**attempt))
                continue

            code, message = self._response_code_and_message(response)

            if response.status_code in {401, 403} and refresh_attempts == 0:
                if self._refresh_access_token_after_401():
                    refresh_attempts += 1
                    continue

            if response.status_code == 429:
                last_error = f"DHAN_HTTP_429:{message}"
                if attempt >= self._MAX_RETRIES:
                    raise RuntimeError(f"DHAN_RATE_LIMITED: {last_error}")
                time.sleep(max(self._RETRY_BASE_SECONDS * (2**attempt), 2.0))
                continue

            if response.status_code >= 400:
                raise RuntimeError(f"DHAN_HTTP_{response.status_code}: {code} {message} [{self._auth_context()}]")

            try:
                return response.json()
            except ValueError:
                return response.text

        raise RuntimeError(f"DHAN_REQUEST_FAILED: {last_error or 'unknown error'}")

    def health(self) -> BrokerHealth:
        try:
            self._request("GET", "/v2/funds/limits", category="data")
            return BrokerHealth(True, True, "DHAN API AUTHENTICATED")
        except Exception as exc:
            return BrokerHealth(False, False, str(exc))

    def funds(self) -> float | None:
        try:
            payload = self._request("GET", "/v2/funds/limits", category="data")
        except Exception:
            return None
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                for key in ("availabelBalance", "availableBalance", "sodLimit", "withdrawableBalance"):
                    try:
                        if data.get(key) is not None:
                            return float(data[key])
                    except (TypeError, ValueError):
                        continue
        return None

    def bulk_quotes(self, instruments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        if not instruments:
            return {}
        result: dict[str, dict[str, Any]] = {}
        for start in range(0, len(instruments), 500):
            batch = instruments[start:start + 500]
            by_segment: dict[str, list[dict[str, Any]]] = {}
            for x in batch:
                seg = str(x.get("exchange_segment", "NSE_EQ"))
                by_segment.setdefault(seg, []).append(x)
            for seg, items in by_segment.items():
                body = {seg: [str(x["security_id"]) for x in items]}
                try:
                    payload = self._request("POST", "/v2/marketfeed/quote", category="quote", json=body)
                except Exception:
                    payload = self._request("POST", "/v2/marketfeed/ltp", category="quote", json=body)
                if not isinstance(payload, dict):
                    continue
                data = payload.get("data") or {}
                if isinstance(data, dict):
                    for section in data.values():
                        if isinstance(section, list):
                            for q in section:
                                if isinstance(q, dict):
                                    sid = q.get("securityId", q.get("security_id"))
                                    if sid is not None:
                                        result[str(sid)] = q
                        elif isinstance(section, dict):
                            for sid, q in section.items():
                                if isinstance(q, dict):
                                    result[str(sid)] = q
        return result

    def history(self, security_id: str, exchange_segment: str = "NSE_EQ", interval: int = 5) -> pd.DataFrame:
        end = datetime.now(ZoneInfo("Asia/Kolkata"))
        start = end - timedelta(days=7)
        payload = self._request(
            "POST",
            "/v2/charts/intraday",
            category="data",
            json={
                "securityId": str(security_id),
                "exchangeSegment": exchange_segment,
                "instrument": "EQUITY",
                "interval": str(interval),
                "fromDate": start.strftime("%Y-%m-%d"),
                "toDate": end.strftime("%Y-%m-%d"),
            },
        )
        if not isinstance(payload, dict):
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        ts = data.get("timestamp") or []
        df = pd.DataFrame({
            "timestamp": pd.to_datetime(ts, unit="s", utc=True, errors="coerce"),
            "open": pd.to_numeric(data.get("open", []), errors="coerce"),
            "high": pd.to_numeric(data.get("high", []), errors="coerce"),
            "low": pd.to_numeric(data.get("low", []), errors="coerce"),
            "close": pd.to_numeric(data.get("close", []), errors="coerce"),
            "volume": pd.to_numeric(data.get("volume", []), errors="coerce"),
        })
        return df.dropna(subset=["timestamp", "open", "high", "low", "close"]).reset_index(drop=True)

    def order(self, symbol: str, side: str, quantity: int, price: float, live: bool = False, **kwargs: Any) -> dict[str, Any]:
        if not live or not settings.live_mode_requested:
            raise RuntimeError("Live order submission is disabled; use paper mode.")
        raise RuntimeError("Live execution adapter is intentionally disabled in this deployment.")

    def positions(self) -> list[dict[str, Any]]:
        try:
            payload = self._request("GET", "/v2/positions", category="data")
            data = payload.get("data") if isinstance(payload, dict) else None
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def orders(self) -> list[dict[str, Any]]:
        try:
            payload = self._request("GET", "/v2/orders", category="data")
            data = payload.get("data") if isinstance(payload, dict) else None
            return data if isinstance(data, list) else []
        except Exception:
            return []
