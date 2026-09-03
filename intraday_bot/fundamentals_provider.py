from __future__ import annotations

import os
from typing import Any

import requests


BASE_URL = "https://api.twelvedata.com"


def _secret(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        import streamlit as st
        value = st.secrets.get(name, "")
        return str(value).strip() if value is not None else ""
    except Exception:
        return ""


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.replace(",", "").replace("%", "").strip()
            if not value or value.lower() in {"n/a", "na", "null", "none", "-"}:
                return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _walk(obj: Any, aliases: tuple[str, ...]) -> float | None:
    wanted = {x.lower().replace(" ", "_") for x in aliases}
    if isinstance(obj, dict):
        for key, value in obj.items():
            norm = str(key).lower().replace(" ", "_").replace("-", "_")
            if norm in wanted:
                n = _number(value)
                if n is not None:
                    return n
        for value in obj.values():
            found = _walk(value, aliases)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _walk(value, aliases)
            if found is not None:
                return found
    return None


def _first(obj: Any, *aliases: str) -> float | None:
    return _walk(obj, tuple(aliases))


def _request(endpoint: str, symbol: str, timeout: int = 20) -> dict[str, Any]:
    api_key = _secret("TWELVEDATA_API_KEY")
    if not api_key:
        raise RuntimeError("TWELVEDATA_UNAVAILABLE: TWELVEDATA_API_KEY is not configured")
    url = f"{BASE_URL}/{endpoint}"
    provider_symbol = symbol if ":" in symbol else f"NSE:{symbol}"
    response = requests.get(
        url,
        params={"symbol": provider_symbol, "apikey": api_key},
        timeout=timeout,
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"TWELVEDATA_HTTP_{response.status_code}: non-JSON response") from exc
    if response.status_code >= 400:
        raise RuntimeError(f"TWELVEDATA_HTTP_{response.status_code}: {payload}")
    if isinstance(payload, dict) and str(payload.get("status", "")).lower() == "error":
        message = payload.get("message") or payload.get("code") or "provider error"
        raise RuntimeError(f"TWELVEDATA_ERROR: {message}")
    if not isinstance(payload, dict):
        raise RuntimeError("TWELVEDATA_ERROR: unexpected response")
    return payload


def fetch_fundamentals(symbol: str) -> dict[str, Any]:
    """Fetch source-supplied financial values; never fabricate missing metrics."""
    symbol = str(symbol).strip().upper()
    if not symbol:
        raise ValueError("symbol is required")

    statistics = _request("statistics", symbol)
    result: dict[str, Any] = {
        "symbol": symbol,
        "source": "Twelve Data",
        "source_status": "AVAILABLE",
    }

    mapping: dict[str, tuple[str, ...]] = {
        "pe": ("pe_ratio", "price_to_earnings", "trailing_pe", "forward_pe", "pe"),
        "roe": ("return_on_equity", "roe"),
        "roce": ("return_on_capital_employed", "return_on_capital", "return_on_invested_capital", "roic", "roce"),
        "debt_to_equity": ("debt_to_equity", "debt_equity_ratio"),
        "eps_growth": ("eps_growth", "diluted_eps_growth", "earnings_per_share_growth"),
        "profit_growth": ("profit_growth", "net_income_growth", "net_profit_growth", "earnings_growth"),
        "predictability": ("predictability", "predictability_score", "earnings_predictability"),
        "earnings_quality": ("earnings_quality", "earnings_quality_score"),
    }
    for output_key, aliases in mapping.items():
        value = _first(statistics, *aliases)
        if value is not None:
            result[output_key] = value

    # SCRAP portfolio-concentration fields are retained only when an explicit
    # percentage field exists. We do not guess whether a generic weight is a
    # fraction or a percentage.
    sector = _first(statistics, "sector_weight_pct")
    company = _first(statistics, "company_weight_pct")
    if sector is not None:
        result["sector_weight_pct"] = sector
    if company is not None:
        result["company_weight_pct"] = company

    # Relative strength and market trend are deliberately absent here. They
    # are market/benchmark inputs, not company-fundamental values.
    result["missing_provider_fields"] = [
        key
        for key in (
            "profit_growth", "eps_growth", "roce", "roe", "debt_to_equity",
            "predictability", "earnings_quality", "pe"
        )
        if key not in result
    ]
    return result
