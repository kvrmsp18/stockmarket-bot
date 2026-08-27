"""DhanHQ REST integration with automatic token renewal and broker safety.

Dhan access tokens generated from the Dhan Web portal are treated as the
source of truth. A supplied/env access token is never proactively renewed from
an unrelated local cache timestamp. Token renewal is attempted only after the
live API actually rejects the current token.

The current DhanHQ v2 REST contract uses the access-token and client-id
headers for normal API requests. POST requests also carry dhanClientId in the
JSON payload, matching Dhan's official Python SDK behavior.

Live order placement remains disabled unless DHAN_LIVE_TRADING_ENABLED=true.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import requests
from dotenv import load_dotenv

from .models import MarketSnapshot

load_dotenv()


class DhanAPIError(RuntimeError):
    """Raised when the DhanHQ API returns an error."""


class DhanAuthenticationError(DhanAPIError):
    """Raised when Dhan authentication is unavailable or rejected."""


class DhanLiveTradingDisabled(DhanAPIError):
    """Raised when code attempts a live order while the safety gate is off."""


class DhanHQClient:
    """Small dependency-light DhanHQ client with safe access-token handling."""

    BASE_URL = "https://api.dhan.co/v2"
    AUTH_BASE_URL = "https://auth.dhan.co"

    @staticmethod
    def _safe_base_url(value: str | None, default: str) -> str:
        """Return an absolute API base URL or the known safe default."""
        candidate = (value or "").strip().rstrip("/")
        if not candidate:
            return default.rstrip("/")
        if not candidate.startswith(("https://", "http://")):
            return default.rstrip("/")
        return candidate

    def __init__(
        self,
        client_id: str | None = None,
        access_token: str | None = None,
        base_url: str | None = None,
        auth_base_url: str | None = None,
        timeout: float = 10.0,
        session: requests.Session | None = None,
    ) -> None:
        explicit_token = (access_token or "").strip()
        env_token = os.getenv("DHAN_ACCESS_TOKEN", "").strip()
        env_client_id = os.getenv("DHAN_CLIENT_ID", "").strip()

        self.client_id = (client_id or env_client_id).strip()
        self.base_url = self._safe_base_url(
            base_url if base_url is not None else os.getenv("DHAN_API_BASE_URL"),
            self.BASE_URL,
        )
        self.auth_base_url = self._safe_base_url(
            auth_base_url if auth_base_url is not None else os.getenv("DHAN_AUTH_BASE_URL"),
            self.AUTH_BASE_URL,
        )
        self.timeout = timeout
        self.session = session or requests.Session()
        self.token_file = Path(os.getenv("DHAN_TOKEN_FILE", ".dhan_access_token.json"))

        # Explicit constructor token > environment token > local cache.
        # A supplied/env token is never assigned the cached token's timestamp.
        if explicit_token:
            self.access_token = explicit_token
            self._token_source = "constructor"
            self._token_obtained_at = None
        elif env_token:
            self.access_token = env_token
            self._token_source = "environment"
            self._token_obtained_at = None
        else:
            self.access_token = self._load_cached_token()
            self._token_source = "cache"
            self._token_obtained_at = self._load_cached_token_time()

        self._last_auth_error: str | None = None
        self._renew_attempted = False

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.access_token)

    @property
    def live_trading_enabled(self) -> bool:
        return os.getenv("DHAN_LIVE_TRADING_ENABLED", "false").strip().lower() == "true"

    @property
    def last_auth_error(self) -> str | None:
        return self._last_auth_error

    @property
    def token_source(self) -> str:
        return self._token_source

    def _load_cached_payload(self) -> dict:
        try:
            if not self.token_file.exists():
                return {}
            payload = json.loads(self.token_file.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError):
            return {}

    def _load_cached_token(self) -> str:
        payload = self._load_cached_payload()
        return str(payload.get("access_token") or "").strip()

    def _load_cached_token_time(self) -> datetime | None:
        payload = self._load_cached_payload()
        raw = payload.get("obtained_at")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw)).astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

    def _persist_token(self, token: str, obtained_at: datetime | None = None) -> None:
        payload = {
            "access_token": token,
            "obtained_at": (obtained_at or datetime.now(timezone.utc)).isoformat(),
        }
        try:
            self.token_file.parent.mkdir(parents=True, exist_ok=True)
            self.token_file.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass

    def _headers(self, *, marketfeed: bool = False) -> dict[str, str]:
        if not self.configured:
            raise DhanAuthenticationError(
                "Dhan credentials are not configured. Set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN."
            )

        # Dhan's current official Python SDK sends client-id together with the
        # access-token for normal REST calls. The marketfeed endpoint uses the
        # same client-id header; keeping the flag preserves compatibility with
        # the existing call sites without dropping the required identity.
        headers = {
            "access-token": self.access_token,
            "client-id": self.client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        return headers

    @staticmethod
    def _safe_response_detail(response: requests.Response) -> str:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                detail = (
                    payload.get("remarks")
                    or payload.get("message")
                    or payload.get("error")
                    or payload.get("errorMessage")
                )
                if detail is not None:
                    return str(detail)[:500]
            return json.dumps(payload, sort_keys=True)[:500]
        except ValueError:
            text = response.text.strip()
            return text[:500] if text else "No response body"

    @staticmethod
    def _extract_token(payload: object) -> str:
        if isinstance(payload, dict):
            for key in ("accessToken", "access_token", "token"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            for key in ("data", "result"):
                token = DhanHQClient._extract_token(payload.get(key))
                if token:
                    return token
        return ""

    def renew_token(self) -> str:
        """Renew the currently active token using Dhan's RenewToken endpoint."""
        if not self.client_id or not self.access_token:
            raise DhanAuthenticationError(
                "A client ID and current access token are required to renew Dhan authentication."
            )
        try:
            response = self.session.get(
                urljoin(f"{self.base_url}/", "RenewToken"),
                headers={"access-token": self.access_token, "dhanClientId": self.client_id},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            self._last_auth_error = f"Dhan token renewal network error: {exc}"
            raise DhanAuthenticationError(self._last_auth_error) from exc

        if not response.ok:
            detail = self._safe_response_detail(response)
            self._last_auth_error = (
                f"Dhan token renewal failed with HTTP {response.status_code}: {detail}"
            )
            raise DhanAuthenticationError(self._last_auth_error)

        try:
            payload = response.json()
        except ValueError as exc:
            self._last_auth_error = "Dhan token renewal returned a non-JSON response."
            raise DhanAuthenticationError(self._last_auth_error) from exc

        token = self._extract_token(payload)
        if not token:
            self._last_auth_error = (
                "Dhan token renewal succeeded but no new access token was returned."
            )
            raise DhanAuthenticationError(self._last_auth_error)

        self.access_token = token
        self._token_source = "renewed"
        self._token_obtained_at = datetime.now(timezone.utc)
        self._persist_token(token, self._token_obtained_at)
        self._last_auth_error = None
        self._renew_attempted = False
        return token

    def _token_needs_proactive_renewal(self) -> bool:
        if self._token_source != "cache" or not self._token_obtained_at:
            return False
        return datetime.now(timezone.utc) - self._token_obtained_at >= timedelta(hours=23)

    def _ensure_token(self) -> None:
        if not self.configured:
            raise DhanAuthenticationError(
                "Dhan credentials are not configured. Set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN."
            )
        if self._token_needs_proactive_renewal() and not self._renew_attempted:
            self._renew_attempted = True
            try:
                self.renew_token()
            except DhanAuthenticationError:
                pass

    @staticmethod
    def _with_client_id_payload(client_id: str, payload: object) -> object:
        """Add dhanClientId to Dhan POST payloads without mutating caller data."""
        if not isinstance(payload, dict):
            return payload
        enriched = dict(payload)
        enriched.setdefault("dhanClientId", client_id)
        return enriched

    def _request(self, method: str, path: str, *, marketfeed: bool = False, **kwargs) -> dict:
        self._ensure_token()
        endpoint = path if path.startswith("/") else f"/{path}"
        url = f"{self.base_url}{endpoint}"
        if not url.startswith(("https://", "http://")):
            raise DhanAPIError(f"Dhan API URL is not absolute: {url}")

        if method.upper() in {"POST", "PUT", "PATCH"} and "json" in kwargs:
            kwargs["json"] = self._with_client_id_payload(self.client_id, kwargs["json"])

        for attempt in range(2):
            try:
                response = self.session.request(
                    method,
                    url,
                    headers=self._headers(marketfeed=marketfeed),
                    timeout=self.timeout,
                    **kwargs,
                )
            except requests.RequestException as exc:
                raise DhanAPIError(f"Dhan API network error: {exc}") from exc

            detail = self._safe_response_detail(response)
            invalid_token = response.status_code in (400, 401, 403) and "token" in detail.lower()
            if response.status_code in (401, 403) or invalid_token:
                original_error = (
                    f"Dhan access token was rejected. HTTP {response.status_code}: {detail}"
                )
                self._last_auth_error = original_error
                if attempt == 0 and not self._renew_attempted:
                    self._renew_attempted = True
                    try:
                        self.renew_token()
                        continue
                    except DhanAuthenticationError:
                        pass
                raise DhanAuthenticationError(original_error)

            if not response.ok:
                raise DhanAPIError(f"Dhan API returned HTTP {response.status_code}: {detail}")

            try:
                payload = response.json()
            except ValueError as exc:
                raise DhanAPIError("Dhan API returned a non-JSON response.") from exc

            if isinstance(payload, dict) and str(payload.get("status", "")).lower() == "failure":
                message = (
                    payload.get("remarks")
                    or payload.get("message")
                    or payload.get("error")
                    or "Unknown Dhan API failure"
                )
                raise DhanAPIError(str(message))
            return payload

        raise DhanAuthenticationError("Dhan authentication failed after automatic renewal attempt.")

    def profile(self) -> dict:
        return self._request("GET", "/profile")

    def health(self) -> dict:
        if not self.configured:
            return {"status": "NOT_CONFIGURED", "provider": "dhanhq", "authenticated": False}
        try:
            payload = self.profile()
            data = payload.get("data", {}) if isinstance(payload, dict) else {}
            return {
                "status": "READY",
                "provider": "dhanhq",
                "authenticated": True,
                "client_id": str(data.get("dhanClientId") or self.client_id),
                "token_renewal": "on_api_rejection",
                "token_source": self._token_source,
            }
        except DhanAuthenticationError as exc:
            return {
                "status": "AUTH_EXPIRED_OR_REJECTED",
                "provider": "dhanhq",
                "authenticated": False,
                "error": str(exc),
                "token_renewal": "failed",
                "token_source": self._token_source,
            }
        except DhanAPIError as exc:
            return {
                "status": "API_ERROR",
                "provider": "dhanhq",
                "authenticated": False,
                "error": str(exc),
                "token_source": self._token_source,
            }

    def fund_limits(self) -> dict:
        return self._request("GET", "/fundlimit")

    @staticmethod
    def _fund_data(payload: object) -> dict:
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                return data
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        return item
            return payload
        return {}

    @staticmethod
    def available_funds(payload: dict) -> float:
        data = DhanHQClient._fund_data(payload)
        if not data:
            raise DhanAPIError("Dhan fund-limit API returned no usable data.")
        aliases = (
            "availableBalance",
            "availabelBalance",
            "available_balance",
            "availableFunds",
            "available_funds",
            "netAvailableBalance",
            "netAvailableMargin",
            "availableMargin",
            "cashAvailable",
            "withdrawableBalance",
        )
        for key in aliases:
            if key not in data or data.get(key) is None:
                continue
            try:
                return max(0.0, float(data[key]))
            except (TypeError, ValueError) as exc:
                raise DhanAPIError(f"Dhan fund-limit field '{key}' is not numeric.") from exc
        keys = ", ".join(sorted(str(key) for key in data.keys()))[:500]
        raise DhanAPIError(
            "Dhan fund-limit response was received but no supported available-funds "
            f"field was found. Returned fields: {keys or 'none'}"
        )

    def positions(self) -> dict:
        return self._request("GET", "/positions")

    def orders(self) -> dict:
        return self._request("GET", "/orders")

    def order(self, order_id: str) -> dict:
        return self._request("GET", f"/orders/{order_id}")

    def tradebook(self, order_id: str | None = None) -> dict:
        path = f"/trades/{order_id}" if order_id else "/trades"
        return self._request("GET", path)

    def margin_calculator(
        self,
        *,
        security_id: str,
        exchange_segment: str,
        transaction_type: str,
        quantity: int,
        product_type: str = "INTRADAY",
        price: float,
        trigger_price: float = 0.0,
    ) -> dict:
        return self._request(
            "POST",
            "/margincalculator",
            json={
                "securityId": str(security_id),
                "exchangeSegment": exchange_segment.upper(),
                "transactionType": transaction_type.upper(),
                "quantity": int(quantity),
                "productType": product_type.upper(),
                "price": float(price),
                "triggerPrice": float(trigger_price),
            },
        )

    def place_intraday_equity_order(
        self,
        *,
        security_id: str,
        exchange_segment: str,
        transaction_type: str,
        quantity: int,
        order_type: str = "MARKET",
        price: float = 0.0,
        correlation_id: str | None = None,
    ) -> dict:
        if not self.live_trading_enabled:
            raise DhanLiveTradingDisabled(
                "Live trading is disabled. Set DHAN_LIVE_TRADING_ENABLED=true only after the paper-validation gate is passed."
            )
        if quantity <= 0:
            raise ValueError("Order quantity must be positive.")

        payload = {
            "transactionType": transaction_type.upper(),
            "exchangeSegment": exchange_segment.upper(),
            "productType": "INTRADAY",
            "orderType": order_type.upper(),
            "validity": "DAY",
            "securityId": str(security_id),
            "quantity": int(quantity),
            "disclosedQuantity": 0,
            "price": float(price),
            "afterMarketOrder": False,
            "triggerPrice": 0.0,
        }
        if correlation_id:
            payload["correlationId"] = correlation_id[:50]
        return self._request("POST", "/orders", json=payload)

    def ltp(self, instruments: dict[str, list[int]]) -> dict:
        if not instruments:
            raise ValueError("At least one Dhan exchange/security-id group is required.")
        return self._request("POST", "/marketfeed/ltp", marketfeed=True, json=instruments)

    def snapshots(
        self,
        symbols: Iterable[str],
        security_map: dict[str, dict[str, int]],
    ) -> list[MarketSnapshot]:
        requested = list(symbols)
        if not requested:
            return []

        grouped: dict[str, list[int]] = {}
        for symbol in requested:
            mapping = security_map.get(symbol)
            if not mapping:
                raise KeyError(f"No Dhan security mapping configured for symbol '{symbol}'.")
            exchange = str(mapping["exchange"]).upper()
            security_id = int(mapping["security_id"])
            grouped.setdefault(exchange, []).append(security_id)

        payload = self.ltp(grouped)
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        timestamp = datetime.now(timezone.utc)
        result: list[MarketSnapshot] = []

        for symbol in requested:
            mapping = security_map[symbol]
            exchange = str(mapping["exchange"]).upper()
            security_id = str(int(mapping["security_id"]))
            exchange_data = data.get(exchange, {}) if isinstance(data, dict) else {}
            quote = exchange_data.get(security_id, {}) if isinstance(exchange_data, dict) else {}
            if not isinstance(quote, dict) or quote.get("last_price") is None:
                raise DhanAPIError(
                    f"Dhan LTP response did not contain last_price for {symbol} ({exchange}/{security_id})."
                )

            def optional_float(key: str) -> float | None:
                value = quote.get(key)
                try:
                    return float(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            volume = quote.get("volume")
            try:
                volume_value = int(volume) if volume is not None else None
            except (TypeError, ValueError):
                volume_value = None

            result.append(
                MarketSnapshot(
                    symbol=symbol,
                    exchange=exchange,
                    timestamp=timestamp,
                    last_price=float(quote["last_price"]),
                    previous_close=optional_float("previous_close"),
                    volume=volume_value,
                    bid=optional_float("bid_price"),
                    ask=optional_float("ask_price"),
                )
            )
        return result


def load_security_map_from_env() -> dict[str, dict[str, int]]:
    """Load optional Dhan symbol/security mappings from JSON environment data."""
    raw = os.getenv("DHAN_SECURITY_IDS_JSON", "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("DHAN_SECURITY_IDS_JSON must contain valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("DHAN_SECURITY_IDS_JSON must be a JSON object.")

    result: dict[str, dict[str, int]] = {}
    for symbol, mapping in payload.items():
        if not isinstance(mapping, dict):
            raise ValueError(f"Dhan mapping for {symbol} must be an object.")
        exchange = str(mapping.get("exchange") or "").strip().upper()
        raw_id = mapping.get("security_id", mapping.get("securityId"))
        if exchange not in {"NSE_EQ", "BSE_EQ"}:
            raise ValueError(f"Dhan mapping for {symbol} has unsupported exchange '{exchange}'.")
        try:
            security_id = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Dhan mapping for {symbol} has an invalid security_id.") from exc
        if security_id <= 0:
            raise ValueError(f"Dhan mapping for {symbol} must have a positive security_id.")
        result[str(symbol).strip().upper()] = {
            "exchange": exchange,
            "security_id": security_id,
        }
    return result
