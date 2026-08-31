from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from typing import Any

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
    def order(self, symbol: str, side: str, quantity: int, price: float, live: bool = False, **kwargs: Any) -> dict[str, Any]: raise NotImplementedError
    def positions(self) -> list[dict[str, Any]]: return []
    def orders(self) -> list[dict[str, Any]]: return []


class PaperTradingBroker(BrokerInterface):
    def __init__(self, capital: float | None = None) -> None:
        self.capital = float(capital or settings.reference_capital)
        self._positions: dict[str, dict[str, Any]] = {}

    def health(self) -> BrokerHealth:
        return BrokerHealth(True, True, "PAPER BROKER READY")

    def funds(self) -> float | None:
        used = sum(float(p["entry"]) * int(p["quantity"]) for p in self._positions.values())
        return max(0.0, self.capital - used)

    def bulk_quotes(self, instruments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        # Paper mode never fabricates live prices. Empty data means DATA_UNAVAILABLE.
        return {}

    def order(self, symbol: str, side: str, quantity: int, price: float, live: bool = False, **kwargs: Any) -> dict[str, Any]:
        if live:
            raise RuntimeError("PaperTradingBroker cannot place live orders")
        order_id = "PAPER-" + uuid.uuid4().hex[:16]
        return {"order_id": order_id, "status": "FILLED", "symbol": symbol, "side": side, "quantity": quantity, "price": price, "mode": "PAPER"}

    def positions(self) -> list[dict[str, Any]]:
        return list(self._positions.values())


class DhanBroker(BrokerInterface):
    """Dhan adapter. Strategy code only sees BrokerInterface; credentials never live in source."""

    def __init__(self) -> None:
        self.client_id = os.getenv("DHAN_CLIENT_ID", "").strip()
        self.token = os.getenv("DHAN_ACCESS_TOKEN", "").strip()
        self.base = settings.dhan_base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"access-token": self.token, "client-id": self.client_id, "Content-Type": "application/json", "Accept": "application/json"})

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if not self.client_id or not self.token:
            raise RuntimeError("DHAN_AUTH_UNAVAILABLE")
        response = self.session.request(method, self.base + path, timeout=15, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"DHAN_HTTP_{response.status_code}: {response.text[:300]}")
        return response.json()

    def health(self) -> BrokerHealth:
        if not self.client_id or not self.token:
            return BrokerHealth(False, False, "DHAN credentials unavailable")
        try:
            self.funds()
            return BrokerHealth(True, True, "DHAN CONNECTED")
        except Exception as exc:
            return BrokerHealth(False, False, str(exc))

    def funds(self) -> float | None:
        data = self._request("GET", "/v2/fundlimit")
        body = data.get("data", data) if isinstance(data, dict) else {}
        for key in ("availabelBalance", "availableBalance", "availabel_balance", "available_balance", "sodLimit"):
            if body.get(key) is not None:
                return float(body[key])
        return None

    def bulk_quotes(self, instruments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Bulk quote request; this is the performance-critical full-universe observation path."""
        groups: dict[str, list[str]] = {}
        for item in instruments:
            ex = item.get("exchange_segment", "NSE_EQ")
            sid = str(item.get("security_id", ""))
            if sid: groups.setdefault(ex, []).append(sid)
        out: dict[str, dict[str, Any]] = {}
        for ex, ids in groups.items():
            for start in range(0, len(ids), 500):
                chunk = ids[start:start+500]
                data = self._request("POST", "/v2/marketfeed/quote", json={ex: chunk})
                body = data.get("data", data) if isinstance(data, dict) else {}
                rows = body.get(ex, body) if isinstance(body, dict) else {}
                if isinstance(rows, dict):
                    for sid, q in rows.items():
                        if isinstance(q, dict): out[str(sid)] = q
        return out

    def order(self, symbol: str, side: str, quantity: int, price: float, live: bool = False, **kwargs: Any) -> dict[str, Any]:
        if not live or not settings.live_mode_requested:
            raise RuntimeError("LIVE_EXECUTION_BLOCKED")
        payload = {"transactionType": side.upper(), "exchangeSegment": kwargs.get("exchange_segment", "NSE_EQ"), "productType": kwargs.get("product_type", "INTRADAY"), "orderType": kwargs.get("order_type", "LIMIT"), "validity": "DAY", "securityId": str(kwargs["security_id"]), "quantity": int(quantity), "price": float(price), "disclosedQuantity": 0, "afterMarketOrder": False}
        return self._request("POST", "/v2/orders", json=payload)

    def positions(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/v2/positions")
        return data.get("data", data) if isinstance(data, dict) else []

    def orders(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/v2/orders")
        return data.get("data", data) if isinstance(data, dict) else []


def load_security_map() -> dict[str, dict[str, Any]]:
    raw = os.getenv("DHAN_SECURITY_IDS_JSON", "").strip()
    if not raw: return {}
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for symbol, value in obj.items():
        if isinstance(value, dict): result[str(symbol).upper()] = value
        else: result[str(symbol).upper()] = {"security_id": value, "exchange_segment": "NSE_EQ"}
    return result
