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
        return [x for x in payload if isinstance(x, dict)]
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
    """Fetch NSE OI-spurt underlying data with a short process cache.

    The human-facing source is NSE's Change in Open Interest / OI Spurts page.
    The collector uses the corresponding NSE data feed when reachable and
    normalizes only source-returned fields. No synthetic derivatives values are
    generated.
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
            "oi_change_pct": _number(_pick(row, "pchangeinopeninterest", "pchangeinoi", "percentchangeinopeninterest", "oichangepct", "changeinoipercent")),
            "oi_change": _number(_pick(row, "changeinopeninterest", "changeinoi")),
            "open_interest": _number(_pick(row, "openinterest", "oi")),
            "volume": _number(_pick(row, "volume", "vol")),
            "value": _number(_pick(row, "value", "totaltradedvalue")),
            "futures_value_lakhs": _number(_pick(row, "futuresvalue", "futuresval", "futures_value", "futuresvalueinlakhs")),
            "options_value_lakhs": _number(_pick(row, "optionsvalue", "optionsval", "options_value", "optionsvalueinlakhs", "optionvalue")),
            "total_value_lakhs": _number(_pick(row, "totalvalue", "totalval", "total_value", "totalvalueinlakhs")),
            "timestamp": _pick(row, "timestamp", "time", "datetime", "asof"),
            "source": "NSE OI Spurts",
        }
        if item["total_value_lakhs"] is None:
            parts = [item.get("futures_value_lakhs"), item.get("options_value_lakhs")]
            if all(isinstance(v, (int, float)) for v in parts):
                item["total_value_lakhs"] = float(parts[0]) + float(parts[1])
        if any(item[k] is not None for k in ("oi_change_pct", "oi_change", "open_interest", "futures_value_lakhs", "options_value_lakhs", "total_value_lakhs")):
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


def market_context(force: bool = False) -> dict[str, Any]:
    """Aggregate the OI-spurt feed into a market-wide derivatives participation view.

    This is a research context only. Aggregate derivatives value/activity does not
    by itself establish market direction.
    """
    try:
        rows = fetch_oi_spurts(force=force)
    except Exception as exc:
        return {"status": "DATA UNAVAILABLE", "source": PAGE_URL, "error": str(exc)}

    def total(field: str) -> float:
        return sum(float(r[field]) for r in rows if isinstance(r.get(field), (int, float)))

    signal_counts = {name: 0 for name in ("LONG_BUILDUP", "SHORT_BUILDUP", "SHORT_COVERING", "LONG_UNWINDING", "UNKNOWN")}
    for row in rows:
        change = row.get("change_pct")
        oi_change = row.get("oi_change_pct")
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
        signal_counts[signal] += 1

    value_rows = [r for r in rows if isinstance(r.get("total_value_lakhs"), (int, float))]
    top_value = sorted(value_rows, key=lambda r: float(r["total_value_lakhs"]), reverse=True)[:10]
    return {
        "status": "AVAILABLE",
        "source": PAGE_URL,
        "symbols": len(rows),
        "futures_value_lakhs": total("futures_value_lakhs"),
        "options_value_lakhs": total("options_value_lakhs"),
        "total_value_lakhs": total("total_value_lakhs"),
        "signal_counts": signal_counts,
        "top_value_symbols": [
            {"symbol": r["symbol"], "total_value_lakhs": r["total_value_lakhs"], "signal": oi_context(r["symbol"]).get("signal", "UNKNOWN")}
            for r in top_value
        ],
    }
