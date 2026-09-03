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


def _text(obj: Any, aliases: tuple[str, ...]) -> str | None:
    wanted = {_norm_key(x) for x in aliases}
    if isinstance(obj, dict):
        for key, value in obj.items():
            if _norm_key(key) in wanted and value not in (None, ""):
                return str(value).strip()
        for value in obj.values():
            found = _text(value, aliases)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _text(value, aliases)
            if found:
                return found
    return None


def _norm_key(value: Any) -> str:
    return str(value).lower().replace(" ", "_").replace("-", "_").replace("/", "_")


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


def _records(payload: Any) -> list[dict[str, Any]]:
    """Find tabular financial records recursively."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("data", "earnings", "income_statement", "balance_sheet", "cash_flow"):
            value = payload.get(key)
            if isinstance(value, list):
                rows = [x for x in value if isinstance(x, dict)]
                if rows:
                    return rows
            if isinstance(value, dict):
                rows = _records(value)
                if rows:
                    return rows
        for value in payload.values():
            rows = _records(value)
            if rows:
                return rows
    return []


def _sort_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(row: dict[str, Any]) -> str:
        for field in ("fiscal_date", "fiscal_date_ending", "period_end", "date", "reported_date", "timestamp"):
            if row.get(field):
                return str(row[field])
        return ""
    return sorted(rows, key=key, reverse=True)


def _request(endpoint: str, symbol: str, timeout: int = 20) -> tuple[dict[str, Any], str]:
    api_key = _secret("TWELVEDATA_API_KEY")
    if not api_key:
        raise RuntimeError("TWELVEDATA_UNAVAILABLE: TWELVEDATA_API_KEY is not configured")

    attempts = [
        {"symbol": symbol, "exchange": "NSE"},
        {"symbol": f"NSE:{symbol}"},
    ]
    errors: list[str] = []
    for params in attempts:
        params["apikey"] = api_key
        try:
            response = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=timeout)
            try:
                payload = response.json()
            except ValueError:
                errors.append(f"{response.status_code}: non-JSON response")
                continue
            if response.status_code >= 400:
                message = payload.get("message") if isinstance(payload, dict) else payload
                errors.append(f"{response.status_code}: {message}")
                continue
            if isinstance(payload, dict) and str(payload.get("status", "")).lower() == "error":
                errors.append(f"{payload.get('code', 'error')}: {payload.get('message', 'provider error')}")
                continue
            if isinstance(payload, dict):
                return payload, str(params["symbol"])
        except requests.RequestException as exc:
            errors.append(str(exc))
    raise RuntimeError(f"TWELVEDATA_{endpoint.upper()}_FAILED: " + " | ".join(errors))


def fetch_fundamentals(symbol: str) -> dict[str, Any]:
    """Fetch source-backed financial data without Twelve Data's paid statistics endpoint.

    Statement ratios are calculated only from values returned by the provider.
    Relative strength and market trend are intentionally excluded because they
    belong to the market/technical layer.
    """
    symbol = str(symbol).strip().upper()
    if not symbol:
        raise ValueError("symbol is required")

    payloads: dict[str, dict[str, Any]] = {}
    sources: dict[str, str] = {}
    endpoint_errors: dict[str, str] = {}
    for endpoint in ("income_statement", "balance_sheet", "cash_flow", "earnings", "profile", "quote"):
        try:
            payload, used_symbol = _request(endpoint, symbol)
            payloads[endpoint] = payload
            sources[endpoint] = used_symbol
        except Exception as exc:
            endpoint_errors[endpoint] = str(exc)

    if not payloads:
        raise RuntimeError("TWELVEDATA_FUNDAMENTALS_FAILED: " + " | ".join(endpoint_errors.values()))

    income = payloads.get("income_statement", {})
    balance = payloads.get("balance_sheet", {})
    cashflow = payloads.get("cash_flow", {})
    earnings = payloads.get("earnings", {})
    profile = payloads.get("profile", {})
    quote = payloads.get("quote", {})

    income_rows = _sort_records(_records(income))
    balance_rows = _sort_records(_records(balance))
    cash_rows = _sort_records(_records(cashflow))
    earning_rows = _sort_records(_records(earnings))

    result: dict[str, Any] = {
        "symbol": symbol,
        "source": "Twelve Data financial statements/earnings/profile/quote",
        "source_status": "AVAILABLE",
        "source_endpoints": sorted(payloads),
        "source_symbols": sources,
        "endpoint_errors": endpoint_errors,
    }

    profile_name = _text(profile, ("name", "company_name", "symbol_name"))
    profile_sector = _text(profile, ("sector", "sector_name"))
    profile_industry = _text(profile, ("industry", "industry_name"))
    if profile_name:
        result["company_name"] = profile_name
    if profile_sector:
        result["sector"] = profile_sector
    if profile_industry:
        result["industry"] = profile_industry

    latest_income = income_rows[0] if income_rows else {}
    previous_income = income_rows[1] if len(income_rows) > 1 else {}
    latest_balance = balance_rows[0] if balance_rows else {}
    previous_balance = balance_rows[1] if len(balance_rows) > 1 else {}
    latest_cash = cash_rows[0] if cash_rows else {}
    latest_earnings = earning_rows[0] if earning_rows else {}

    net_income = _walk(latest_income, ("net_income", "net_income_common_stockholders", "net_income_attributable_to_common_shareholders"))
    previous_net_income = _walk(previous_income, ("net_income", "net_income_common_stockholders", "net_income_attributable_to_common_shareholders"))
    eps = _walk(latest_income, ("diluted_eps", "basic_eps", "eps")) or _walk(latest_earnings, ("eps", "diluted_eps", "eps_actual"))
    previous_eps = _walk(previous_income, ("diluted_eps", "basic_eps", "eps")) or _walk(earning_rows[1] if len(earning_rows) > 1 else {}, ("eps", "diluted_eps", "eps_actual"))

    equity = _walk(latest_balance, ("total_equity", "total_shareholders_equity", "stockholders_equity", "shareholders_equity"))
    previous_equity = _walk(previous_balance, ("total_equity", "total_shareholders_equity", "stockholders_equity", "shareholders_equity"))
    debt = _walk(latest_balance, ("total_debt", "long_term_debt", "short_term_debt", "total_borrowings"))
    cash = _walk(latest_balance, ("cash_and_cash_equivalents", "cash_and_short_term_investments", "cash"))
    ebit = _walk(latest_income, ("ebit", "operating_income", "operating_profit"))
    operating_cash = _walk(latest_cash, ("operating_cash_flow", "cash_flow_from_operating_activities", "net_cash_provided_by_operating_activities"))

    fields: dict[str, float | None] = {
        "eps": eps,
        "profit": net_income,
        "capital": (equity or 0.0) + (debt or 0.0) - (cash or 0.0) if equity is not None else None,
        "profit_growth": _growth(net_income, previous_net_income),
        "eps_growth": _growth(eps, previous_eps),
        "debt_to_equity": (debt / equity) if debt is not None and equity not in (None, 0) else None,
        "roe": (net_income / ((equity + previous_equity) / 2) * 100.0) if net_income is not None and equity is not None and previous_equity not in (None, 0) else ((net_income / equity * 100.0) if net_income is not None and equity not in (None, 0) else None),
        "roce": (ebit / ((equity or 0.0) + (debt or 0.0) - (cash or 0.0)) * 100.0) if ebit is not None and ((equity or 0.0) + (debt or 0.0) - (cash or 0.0)) else None,
        "earnings_quality": (operating_cash / net_income) if operating_cash is not None and net_income not in (None, 0) else None,
    }
    for key, value in fields.items():
        if value is not None:
            result[key] = float(value)

    current_price = _walk(quote, ("price", "close", "last_price", "previous_close"))
    if current_price is not None:
        result["current_price"] = float(current_price)
        if eps not in (None, 0):
            result["pe"] = float(current_price) / float(eps)

    # Predictability is derived only when at least three source periods exist.
    profit_series: list[float] = []
    for row in income_rows[:5]:
        value = _walk(row, ("net_income", "net_income_common_stockholders", "net_income_attributable_to_common_shareholders"))
        if value is not None:
            profit_series.append(value)
    if len(profit_series) >= 3 and all(v > 0 for v in profit_series):
        growths = []
        for idx in range(len(profit_series) - 1):
            previous = profit_series[idx + 1]
            if previous:
                growths.append((profit_series[idx] / previous) - 1.0)
        if growths:
            mean = sum(growths) / len(growths)
            variance = sum((g - mean) ** 2 for g in growths) / len(growths)
            result["predictability"] = max(0.0, min(1.0, 1.0 - variance ** 0.5))

    required = (
        "profit_growth", "eps_growth", "roce", "roe", "debt_to_equity",
        "predictability", "earnings_quality", "pe"
    )
    result["missing_provider_fields"] = [key for key in required if key not in result]

    # Raw responses are retained for auditability only; callers should use
    # normalized source fields above when building research scores.
    result["raw_income_statement"] = income
    result["raw_balance_sheet"] = balance
    result["raw_cash_flow"] = cashflow
    result["raw_earnings"] = earnings
    result["raw_profile"] = profile
    result["raw_quote"] = quote
    return result


def _growth(latest: float | None, previous: float | None) -> float | None:
    if latest is None or previous in (None, 0):
        return None
    return (latest / previous - 1.0) * 100.0
