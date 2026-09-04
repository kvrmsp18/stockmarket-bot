from __future__ import annotations

import csv
import io
import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

NSE_BASE = "https://www.nseindia.com"
NSE_INDEX_URL = NSE_BASE + "/api/equity-stockIndices?index="
NSE_ARCHIVE_BASE = "https://archives.nseindia.com/content/indices/"
NIFTY_INDICES_BASE = "https://www.niftyindices.com/IndexConstituent/"
CACHE_PATH = Path("data/sector_membership.json")
STATS_PATH = Path("data/sector_intelligence.json")
CACHE_TTL_HOURS = 24

# Official NSE/Nifty Indices sectoral indices.  The archive CSV is used as the
# primary constituent source because the live NSE JSON endpoint can return 404
# to cloud-hosted runners even when the index itself is valid.
SECTOR_INDICES = {
    "BANKING": "NIFTY BANK",
    "IT": "NIFTY IT",
    "AUTO": "NIFTY AUTO",
    "PHARMA": "NIFTY PHARMA",
    "FMCG": "NIFTY FMCG",
    "METALS": "NIFTY METAL",
    "REALTY": "NIFTY REALTY",
    "MEDIA": "NIFTY MEDIA",
    "PRIVATE_BANK": "NIFTY PRIVATE BANK",
    "PSU_BANK": "NIFTY PSU BANK",
    "FINANCIAL_SERVICES": "NIFTY FINANCIAL SERVICES",
    "HEALTHCARE": "NIFTY HEALTHCARE INDEX",
    "OIL_GAS": "NIFTY OIL & GAS",
    "CONSUMER_DURABLES": "NIFTY CONSUMER DURABLES",
    "CONSUMPTION": "NIFTY INDIA CONSUMPTION",
}

# Known official constituent-file slugs published by NSE/Nifty Indices.
CONSTITUENT_SLUGS = {
    "BANKING": "ind_niftybanklist.csv",
    "IT": "ind_niftyitlist.csv",
    "AUTO": "ind_niftyautolist.csv",
    "PHARMA": "ind_niftypharmalist.csv",
    "FMCG": "ind_niftyfmcglist.csv",
    "METALS": "ind_niftymetallist.csv",
    "REALTY": "ind_niftyrealtylist.csv",
    "MEDIA": "ind_niftymedialist.csv",
    "PRIVATE_BANK": "ind_nifty_privatebanklist.csv",
    "PSU_BANK": "ind_niftypsubanklist.csv",
    "FINANCIAL_SERVICES": "ind_niftyfinservlist.csv",
    "HEALTHCARE": "ind_niftyhealthcarelist.csv",
    "OIL_GAS": "ind_niftyoilgaslist.csv",
    "CONSUMER_DURABLES": "ind_niftyconsumerdurableslist.csv",
    "CONSUMPTION": "ind_niftyconsumptionlist.csv",
}

PRIORITY = [
    "BANKING", "PRIVATE_BANK", "PSU_BANK", "IT", "AUTO", "PHARMA", "FMCG",
    "METALS", "REALTY", "MEDIA", "HEALTHCARE", "OIL_GAS", "CONSUMER_DURABLES",
    "CONSUMPTION", "FINANCIAL_SERVICES",
]


def _normalise_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _load_cache() -> dict[str, Any] | None:
    if not CACHE_PATH.exists():
        return None
    try:
        payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(str(payload.get("fetched_at")).replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - fetched > timedelta(hours=CACHE_TTL_HOURS):
            return None
        return payload
    except Exception:
        return None


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": NSE_BASE + "/",
        "Connection": "keep-alive",
    })
    try:
        s.get(NSE_BASE, timeout=15)
    except requests.RequestException:
        # The API/archives may still be reachable even when the NSE landing
        # page cannot be established from a cloud runner.
        pass
    return s


def _symbols_from_csv(text: str) -> list[str]:
    # Nifty Indices files are CSVs with a Symbol column.  Handle BOMs, quoted
    # fields, and harmless preamble/whitespace without inventing symbols.
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not reader.fieldnames:
        return []
    fields = {str(name).strip().lower(): name for name in reader.fieldnames if name}
    symbol_field = fields.get("symbol") or fields.get("ticker")
    if not symbol_field:
        return []
    symbols: list[str] = []
    for row in reader:
        symbol = _normalise_symbol(row.get(symbol_field))
        if symbol and symbol not in symbols and symbol not in {"SYMBOL", "TICKER"}:
            symbols.append(symbol)
    return symbols


def _fetch_nse_api(session: requests.Session, index_name: str) -> list[str]:
    url = NSE_INDEX_URL + quote(index_name, safe="")
    response = session.get(url, timeout=20)
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    symbols: list[str] = []
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict):
                symbol = _normalise_symbol(row.get("symbol"))
                if symbol and symbol not in symbols:
                    symbols.append(symbol)
    if not symbols:
        raise RuntimeError(f"NSE_SECTOR_MEMBERSHIP_EMPTY:{index_name}")
    return symbols


def _fetch_csv(session: requests.Session, base: str, slug: str) -> list[str]:
    response = session.get(base + slug, timeout=20, headers={
        "Accept": "text/csv,application/octet-stream,text/plain,*/*",
        "Referer": base,
    })
    response.raise_for_status()
    symbols = _symbols_from_csv(response.text)
    if not symbols:
        raise RuntimeError(f"NSE_SECTOR_CSV_EMPTY:{slug}")
    return symbols


def _fetch_index_membership() -> tuple[dict[str, list[str]], dict[str, str], dict[str, str]]:
    session = _session()
    memberships: dict[str, list[str]] = {}
    sources: dict[str, str] = {}
    errors: dict[str, str] = {}

    for sector_name, index_name in SECTOR_INDICES.items():
        slug = CONSTITUENT_SLUGS.get(sector_name)
        attempts: list[tuple[str, Any]] = []
        if slug:
            attempts.append(("NSE_ARCHIVE_CSV", lambda slug=slug: _fetch_csv(session, NSE_ARCHIVE_BASE, slug)))
            attempts.append(("NIFTY_INDICES_CSV", lambda slug=slug: _fetch_csv(session, NIFTY_INDICES_BASE, slug)))
        attempts.append(("NSE_INDEX_API", lambda index_name=index_name: _fetch_nse_api(session, index_name)))

        last_error: Exception | None = None
        for source, loader in attempts:
            try:
                symbols = loader()
                memberships[sector_name] = symbols
                sources[sector_name] = source
                break
            except Exception as exc:
                last_error = exc
        else:
            errors[sector_name] = str(last_error or "unknown error")

    if errors:
        details = "; ".join(f"{k}={v}" for k, v in errors.items())
        raise RuntimeError(f"NSE_SECTOR_MEMBERSHIP_UNAVAILABLE:{details}")
    return memberships, sources, errors


def membership(force_refresh: bool = False) -> dict[str, Any]:
    if not force_refresh:
        cached = _load_cache()
        if cached:
            return cached
    try:
        raw, sources, _ = _fetch_index_membership()
        symbol_sector: dict[str, str] = {}
        symbol_sources: dict[str, list[str]] = {}
        for sector_name in PRIORITY:
            for symbol in raw.get(sector_name, []):
                symbol_sources.setdefault(symbol, []).append(sector_name)
                symbol_sector.setdefault(symbol, sector_name)
        payload = {
            "status": "AVAILABLE",
            "source": "NSE_OFFICIAL_SECTOR_INDICES",
            "source_by_sector": sources,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "sector_indices": SECTOR_INDICES,
            "sectors": raw,
            "symbol_sector": symbol_sector,
            "symbol_sources": symbol_sources,
            "classified_symbols": len(symbol_sector),
        }
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload
    except Exception as exc:
        cached = _load_cache()
        if cached:
            cached["cache_warning"] = str(exc)
            return cached
        raise RuntimeError(f"SECTOR_MEMBERSHIP_UNAVAILABLE:{exc}") from exc


def build(universe: list[dict[str, Any]], qmap: dict[str, dict[str, Any]], force_membership_refresh: bool = False) -> dict[str, Any]:
    cache = membership(force_membership_refresh)
    symbol_sector = cache.get("symbol_sector") or {}
    by_symbol = {str(x.get("symbol", "")).upper(): x for x in universe}
    rows: dict[str, dict[str, Any]] = {}
    for sector_name in PRIORITY:
        members = [s for s in cache.get("sectors", {}).get(sector_name, []) if s in by_symbol]
        changes: list[float] = []
        advancing = declining = unchanged = quoted = 0
        for symbol in members:
            item = by_symbol[symbol]
            q = qmap.get(str(item.get("security_id")), {})
            last = q.get("last_price", q.get("ltp", q.get("lastPrice")))
            prev = q.get("prev_close", q.get("previousClose", (q.get("ohlc") or {}).get("close")))
            try:
                last_f, prev_f = float(last), float(prev)
                if prev_f <= 0:
                    continue
                change = (last_f / prev_f - 1.0) * 100.0
            except (TypeError, ValueError):
                continue
            quoted += 1
            changes.append(change)
            if change > 0.05:
                advancing += 1
            elif change < -0.05:
                declining += 1
            else:
                unchanged += 1
        breadth_den = advancing + declining
        breadth = (advancing / breadth_den * 100.0) if breadth_den else 50.0
        avg = statistics.fmean(changes) if changes else None
        median = statistics.median(changes) if changes else None
        strength = 5.0
        if changes:
            strength += max(-2.0, min(2.0, (avg or 0.0) * 1.5))
            strength += max(-1.5, min(1.5, ((breadth - 50.0) / 20.0)))
        strength = max(0.0, min(10.0, strength))
        state = "STRONG" if strength >= 6.5 else "WEAK" if strength <= 3.5 else "NEUTRAL"
        rows[sector_name] = {
            "index": SECTOR_INDICES[sector_name],
            "members": len(members),
            "quoted": quoted,
            "advancing": advancing,
            "declining": declining,
            "unchanged": unchanged,
            "breadth_pct": round(breadth, 2),
            "average_change_pct": round(avg, 4) if avg is not None else None,
            "median_change_pct": round(median, 4) if median is not None else None,
            "strength": round(strength, 3),
            "state": state,
        }
    classified = len(symbol_sector)
    result = {
        "status": "AVAILABLE",
        "source": cache.get("source", "NSE_OFFICIAL_SECTOR_INDICES"),
        "source_by_sector": cache.get("source_by_sector", {}),
        "membership_fetched_at": cache.get("fetched_at"),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "classified_symbols": classified,
        "unclassified_universe_symbols": max(0, len(by_symbol) - len(set(by_symbol) & set(symbol_sector))),
        "sectors": rows,
        "cache_warning": cache.get("cache_warning"),
    }
    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATS_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def sector_for(symbol: str, cache: dict[str, Any]) -> str | None:
    return (cache.get("symbol_sector") or {}).get(_normalise_symbol(symbol))
