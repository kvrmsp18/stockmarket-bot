from __future__ import annotations

import json
import time
from typing import Any
from urllib.request import Request, urlopen


PAGE_URL = "https://www.nseindia.com/market-data/pre-open-market-cm-and-emerge-market"
API_URL = "https://www.nseindia.com/api/market-data-pre-open?key=ALL"
DEFAULT_TTL_SECONDS = 300

_cache: dict[str, Any] = {"at": 0.0, "rows": [], "summary": {}}


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


def _pick(row: dict[str, Any], *keys: str) -> Any:
    norm = {str(k).lower().replace(" ", "").replace("_", ""): v for k, v in row.items()}
    for key in keys:
        value = norm.get(key.lower().replace(" ", "").replace("_", ""))
        if value is not None:
            return value
    return None


def _find_rows(payload: Any) -> list[dict[str, Any]]:
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
        return json.loads(response.read().decode("utf-8"))


def _classify(change_pct: float | None) -> str:
    if not isinstance(change_pct, (int, float)):
        return "UNKNOWN"
    if change_pct >= 2.0:
        return "STRONG_POSITIVE"
    if change_pct > 0:
        return "POSITIVE"
    if change_pct <= -2.0:
        return "STRONG_NEGATIVE"
    if change_pct < 0:
        return "NEGATIVE"
    return "UNCHANGED"


def fetch_preopen(force: bool = False, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> list[dict[str, Any]]:
    """Fetch NSE cash-market pre-open data and normalize source-returned fields.

    The feed is treated as opening-context evidence only. No trade signal is
    created directly from pre-open data.
    """
    now = time.time()
    if not force and _cache["rows"] and now - float(_cache["at"]) < ttl_seconds:
        return list(_cache["rows"])

    payload = _request_json(API_URL)
    rows = _find_rows(payload)
    normalized: list[dict[str, Any]] = []
    for row in rows:
        symbol = _pick(row, "symbol", "symbolname", "meta.symbol", "identifier")
        symbol = str(symbol or "").strip().upper()
        if not symbol or symbol in {"NIFTY 50", "NIFTY50", "NIFTY"}:
            continue
        prev_close = _number(_pick(row, "previousClose", "prevClose", "prev_close"))
        iep = _number(_pick(row, "iep", "indicativeEquityPrice", "indicativePrice", "price"))
        change = _number(_pick(row, "change", "absoluteChange", "netChange"))
        change_pct = _number(_pick(row, "pChange", "percentChange", "changePercent", "xChange"))
        if change_pct is None and iep is not None and prev_close not in (None, 0):
            change_pct = (iep / prev_close - 1.0) * 100.0
        item = {
            "symbol": symbol,
            "prev_close": prev_close,
            "iep": iep,
            "change": change,
            "change_pct": change_pct,
            "classification": _classify(change_pct),
            "final_price": _number(_pick(row, "finalPrice", "price")),
            "final_quantity": _number(_pick(row, "finalQuantity", "quantity", "finalQty")),
            "total_buy_qty": _number(_pick(row, "totalBuyQuantity", "buyQuantity")),
            "total_sell_qty": _number(_pick(row, "totalSellQuantity", "sellQuantity")),
            "timestamp": _pick(row, "lastUpdateTime", "timestamp", "time"),
            "source": "NSE Pre-Open Market",
        }
        if any(item[k] is not None for k in ("iep", "change_pct", "final_price", "final_quantity", "total_buy_qty", "total_sell_qty")):
            normalized.append(item)

    _cache["at"] = now
    _cache["rows"] = normalized
    return list(normalized)


def _breadth(rows: list[dict[str, Any]]) -> dict[str, Any]:
    advances = sum(1 for r in rows if isinstance(r.get("change_pct"), (int, float)) and r["change_pct"] > 0)
    declines = sum(1 for r in rows if isinstance(r.get("change_pct"), (int, float)) and r["change_pct"] < 0)
    unchanged = sum(1 for r in rows if r.get("change_pct") == 0)
    total = advances + declines + unchanged
    ratio = (advances / declines) if declines else None
    return {
        "advances": advances,
        "declines": declines,
        "unchanged": unchanged,
        "total": total,
        "advance_decline_ratio": ratio,
    }


def market_context(force: bool = False) -> dict[str, Any]:
    """Build market-opening regime context from the pre-open cash-market feed."""
    try:
        rows = fetch_preopen(force=force)
    except Exception as exc:
        return {"status": "DATA UNAVAILABLE", "source": PAGE_URL, "error": str(exc)}

    breadth = _breadth(rows)
    pct_values = [r["change_pct"] for r in rows if isinstance(r.get("change_pct"), (int, float))]
    avg_change = sum(pct_values) / len(pct_values) if pct_values else None
    strong_positive = sum(1 for r in rows if r.get("classification") == "STRONG_POSITIVE")
    strong_negative = sum(1 for r in rows if r.get("classification") == "STRONG_NEGATIVE")

    if breadth["advances"] > breadth["declines"] * 1.5 and (avg_change is None or avg_change > 0):
        regime = "BULLISH_PREOPEN"
    elif breadth["declines"] > breadth["advances"] * 1.5 and (avg_change is None or avg_change < 0):
        regime = "BEARISH_PREOPEN"
    elif breadth["advances"] or breadth["declines"]:
        regime = "MIXED_PREOPEN"
    else:
        regime = "DATA_UNAVAILABLE"

    leaders = sorted(
        [r for r in rows if isinstance(r.get("change_pct"), (int, float))],
        key=lambda x: float(x["change_pct"]),
        reverse=True,
    )[:10]
    laggards = sorted(
        [r for r in rows if isinstance(r.get("change_pct"), (int, float))],
        key=lambda x: float(x["change_pct"]),
    )[:10]

    summary = {
        "status": "AVAILABLE",
        "source": PAGE_URL,
        "symbols": len(rows),
        "breadth": breadth,
        "average_change_pct": avg_change,
        "strong_positive_count": strong_positive,
        "strong_negative_count": strong_negative,
        "regime": regime,
        "leaders": leaders,
        "laggards": laggards,
    }
    _cache["summary"] = summary
    return summary


def stock_context(symbol: str, force: bool = False) -> dict[str, Any]:
    symbol = str(symbol).strip().upper()
    try:
        rows = fetch_preopen(force=force)
    except Exception as exc:
        return {"status": "DATA UNAVAILABLE", "source": PAGE_URL, "symbol": symbol, "error": str(exc)}
    matches = [r for r in rows if r.get("symbol") == symbol]
    if not matches:
        return {"status": "NOT_IN_PREOPEN", "source": PAGE_URL, "symbol": symbol}
    return {"status": "AVAILABLE", **matches[0]}
