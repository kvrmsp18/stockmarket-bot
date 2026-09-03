from __future__ import annotations

import json
import time
from typing import Any
from urllib.request import Request, urlopen


PAGE_URL = "https://www.nseindia.com/market-data/oi-spurts"
API_URL = "https://www.nseindia.com/api/live-analysis-oi-spurts-underlyings"
DEFAULT_TTL_SECONDS = 300

_cache: dict[str, Any] = {"at": 0.0, "rows": []}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, str):
            value = value.replace(",", "").replace("%", "").strip()
            if value.lower() in {"", "na", "n/a", "null", "none", "-"}:
                return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _norm_symbol(value: Any) -> str:
    return str(value or "").strip().upper().replace("&", "&")


def _find_rows(payload: Any) -> list[dict[str, Any]]:
    """Recursively find the first list of record-like dictionaries."""
    if isinstance(payload, list):
        rows = [x for x in payload if isinstance(x, dict)]
        return rows
    if isinstance(payload, dict):
        for key in ("data", "records", "aaData", "rows", "results"):
            value = payload.get(key)
            rows = _find_rows(value)
            if rows:
                return rows
        for value in payload.values():
            rows = _find_rows(value)
            if rows:
                return rows
    return []


def _pick(row: dict[str, Any], *keys: str) -> Any:
    norm = {str(k).lower().replace(" ", "").replace("_", ""): v for k, v in row.items()}
    for key in keys:
        value = norm.get(key.lower().replace(" ", "").replace("_", ""))
        if value is not None:
            return value
    return None


def _request_json(url: str, timeout: int = 20) -> Any:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Referer": PAGE_URL,
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8"))


def fetch_oi_spurts(force: bool = False, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> list[dict[str, Any]]:
    """Fetch NSE's OI-spurt underlying feed with a short process cache.

    The NSE page is the human-facing source. The collector uses the corresponding
    public NSE data feed when reachable, and normalizes only source-returned fields.
    No synthetic OI values are generated.
    """
    now = time.time()
    if not force and _cache["rows"] and now - float(_cache["at"]) < ttl_seconds:
        return list(_cache["rows"])

    payload = _request_json(API_URL)
    rows = _find_rows(payload)
    normalized: list[dict[str, Any]] = []
    for row in rows:
        symbol = _pick(row, "symbol", "symbolname", "underlying", "underlyingvalue")
        symbol = _norm_symbol(symbol)
        if not symbol:
            continue
        item = {
            "symbol": symbol,
            "ltp": _number(_pick(row, "ltp", "lastprice", "lasttradedprice")),
            "change_pct": _number(_pick(row, "pchange", "percentchange", "changepercent")),
            "oi_change_pct": _number(_pick(row, "pchangeinopeninterest", "percentchangeinopeninterest", "oichangepct", "changeinoipercent")),
            "oi_change": _number(_pick(row, "changeinopeninterest", "changeinoi")),
            "open_interest": _number(_pick(row, "openinterest", "oi")),
            "volume": _number(_pick(row, "volume", "vol")),
            "value": _number(_pick(row, "value", "totaltradedvalue")),
            "timestamp": _pick(row, "timestamp", "time", "datetime"),
            "source": "NSE OI Spurts",
        }
        if any(item[k] is not None for k in ("oi_change_pct", "oi_change", "open_interest")):
            normalized.append(item)

    _cache["at"] = now
    _cache["rows"] = normalized
    return list(normalized)


def oi_context(symbol: str, force: bool = False) -> dict[str, Any]:
    """Return normalized OI-spurt context for one underlying, or DATA UNAVAILABLE."""
    symbol = _norm_symbol(symbol)
    try:
        rows = fetch_oi_spurts(force=force)
    except Exception as exc:
        return {
            "status": "DATA UNAVAILABLE",
            "source": PAGE_URL,
            "error": str(exc),
            "symbol": symbol,
        }

    matches = [r for r in rows if r.get("symbol") == symbol]
    if not matches:
        return {
            "status": "NOT_IN_OI_SPURTS",
            "source": PAGE_URL,
            "symbol": symbol,
        }

    item = dict(matches[0])
    change = item.get("change_pct")
    oi_change = item.get("oi_change_pct")
    signal = "UNKNOWN"
    if isinstance(change, (int, float)) and isinstance(oi_change, (int, float)):
        if change > 0 and oi_change > 0:
            signal = "LONG_BUILDUP"
        elif change < 0 and oi_change > 0:
            signal = "SHORT_BUILDUP"
        elif change > 0 and oi_change < 0:
            signal = "SHORT_COVERING"
        elif change < 0 and oi_change < 0:
            signal = "LONG_UNWINDING"

    item.update({"status": "AVAILABLE", "signal": signal})
    return item
