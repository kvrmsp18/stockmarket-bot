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


def _norm_key(value: Any) -> str:
    return str(value).lower().replace(" ", "_").replace("-", "_")


def _walk(obj: Any, aliases: tuple[str, ...]) -> float | None:
    wanted = {_norm_key(x) for x in aliases}
    if isinstance(obj, dict):
        for key, value in obj.items():
            if _norm_key(key) in wanted:
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

    exchange = (_secret("TWELVEDATA_EXCHANGE") or "NSE").strip().upper()
    candidates = [(symbol, exchange)] if ":" in symbol else [(symbol, exchange), (f"{exchange}:{symbol}", "")]
    errors: list[str] = []

    for provider_symbol, provider_exchange in candidates:
        params = {"symbol": provider_symbol, "apikey": api_key}
        if provider_exchange:
            params["exchange"] = provider_exchange
        try:
            response = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=timeout)
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
        except Exception as exc:
            errors.append(str(exc))

    raise RuntimeError("TWELVEDATA_REQUEST_FAILED: " + " | ".join(errors[-2:]))


def _try_request(endpoint: str, symbol: str) -> dict[str, Any] | None:
    try:
        return _request(endpoint, symbol)
    except Exception:
        return None


def _growth_from_series(values: list[float]) -> float | None:
    if len(values) < 2 or values[1] == 0:
        return None
    return (values[0] / values[1] - 1.0) * 100.0


def _series_values(payload: Any, aliases: tuple[str, ...]) -> list[float]:
    wanted = {_norm_key(x) for x in aliases}
    values: list[float] = []

    def visit(obj: Any) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if _norm_key(key) in wanted:
                    n = _number(value)
                    if n is not None:
                        values.append(n)
                visit(value)
        elif isinstance(obj, list):
            for value in obj:
                visit(value)

    visit(payload)
    return values


def fetch_fundamentals(symbol: str) -> dict[str, Any]:
    """Fetch source-supplied company fundamentals without fabricating metrics."""
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
        "pe": ("pe_ratio", "pe_ratio_ttm", "price_to_earnings", "price_to_earnings_ttm", "trailing_pe", "forward_pe", "pe"),
        "roe": ("return_on_equity", "return_on_equity_ttm", "roe", "roe_ttm"),
        "roce": ("return_on_capital_employed", "return_on_capital", "return_on_capital_ttm", "return_on_invested_capital", "roic", "roce"),
        "debt_to_equity": ("debt_to_equity", "debt_to_equity_ratio", "debt_equity_ratio"),
        "eps_growth": ("eps_growth", "eps_growth_yoy", "diluted_eps_growth", "earnings_per_share_growth", "earnings_growth"),
        "profit_growth": ("profit_growth", "profit_growth_yoy", "net_income_growth", "net_profit_growth", "earnings_growth"),
        "predictability": ("predictability", "predictability_score", "earnings_predictability"),
        "earnings_quality": ("earnings_quality", "earnings_quality_score"),
    }
    for output_key, aliases in mapping.items():
        value = _first(statistics, *aliases)
        if value is not None:
            result[output_key] = value

    earnings = _try_request("earnings", symbol)
    if earnings:
        if "eps_growth" not in result:
            growth = _growth_from_series(_series_values(earnings, ("eps", "diluted_eps", "reported_eps", "actual_eps")))
            if growth is not None:
                result["eps_growth"] = growth
        if "profit_growth" not in result:
            growth = _growth_from_series(_series_values(earnings, ("net_income", "net_profit", "profit")))
            if growth is not None:
                result["profit_growth"] = growth
        result["earnings_source_checked"] = True

    profile = _try_request("profile", symbol)
    if profile:
        for key in ("sector", "industry", "theme"):
            value = profile.get(key)
            if value is not None and str(value).strip():
                result[key] = str(value).strip()

    # Only explicit percentage fields can drive the SCRAP concentration gate.
    sector = _first(statistics, "sector_weight_pct")
    company = _first(statistics, "company_weight_pct")
    if sector is not None:
        result["sector_weight_pct"] = sector
    if company is not None:
        result["company_weight_pct"] = company

    # Relative strength and market trend are market/benchmark factors and are
    # deliberately left to the market-data layer.
    required = ("profit_growth", "eps_growth", "roce", "roe", "debt_to_equity", "predictability", "earnings_quality", "pe")
    result["missing_provider_fields"] = [key for key in required if key not in result]
    return result
