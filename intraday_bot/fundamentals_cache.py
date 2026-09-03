from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fundamentals_provider import fetch_fundamentals


CACHE_PATH = Path("data/fundamentals.json")
_DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60
_DEFAULT_REFRESH_PER_CYCLE = 2
_LOCK = threading.Lock()


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _ttl_seconds() -> int:
    return _int_env("FUNDAMENTALS_REFRESH_SECONDS", _DEFAULT_TTL_SECONDS)


def _refresh_per_cycle() -> int:
    return _int_env("FUNDAMENTALS_REFRESH_PER_CYCLE", _DEFAULT_REFRESH_PER_CYCLE)


def _load() -> dict[str, dict[str, Any]]:
    if not CACHE_PATH.exists():
        return {}
    try:
        payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(k).upper(): dict(v) for k, v in payload.items() if isinstance(v, dict)}


def _write(payload: dict[str, dict[str, Any]]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="fundamentals-", suffix=".tmp", dir=str(CACHE_PATH.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
    os.replace(tmp, CACHE_PATH)


def _fresh(record: dict[str, Any]) -> bool:
    fetched = record.get("fetched_at")
    if not fetched:
        return False
    try:
        ts = datetime.fromisoformat(str(fetched).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds()
        return age >= 0 and age < _ttl_seconds()
    except (TypeError, ValueError):
        return False


def _with_current_price(record: dict[str, Any], current_price: float | None) -> dict[str, Any]:
    result = dict(record)
    if current_price is not None:
        try:
            price = float(current_price)
        except (TypeError, ValueError):
            price = 0.0
        if price > 0 and isinstance(result.get("eps"), (int, float)) and float(result["eps"]) != 0:
            result["current_price"] = price
            result["pe"] = price / float(result["eps"])
    return result


def refresh_batch(items: list[tuple[str, float | None]]) -> dict[str, dict[str, Any]]:
    """Refresh a small bounded number of stale symbols, then return the cache.

    Fundamentals change much more slowly than intraday quotes. Persisting a
    seven-day cache avoids repeatedly spending provider requests during every
    five-minute market cycle while still gradually covering the rotating deep
    analysis universe.
    """
    with _LOCK:
        cache = _load()
        budget = _refresh_per_cycle()
        refreshed = 0
        for symbol, price in items:
            key = str(symbol).strip().upper()
            if not key or refreshed >= budget:
                break
            existing = cache.get(key, {})
            if _fresh(existing):
                continue
            try:
                source = fetch_fundamentals(key, current_price=price)
            except Exception as exc:
                # Keep the last verified snapshot when a provider request fails.
                if existing:
                    existing = dict(existing)
                    existing["last_refresh_error"] = str(exc)
                    cache[key] = existing
                continue
            source["fetched_at"] = datetime.now(timezone.utc).isoformat()
            source["cache_source"] = "Twelve Data"
            cache[key] = source
            refreshed += 1
        _write(cache)
        return cache


def get(symbol: str, current_price: float | None = None) -> dict[str, Any]:
    key = str(symbol).strip().upper()
    if not key:
        return {}
    with _LOCK:
        record = _load().get(key, {})
    return _with_current_price(record, current_price)
