from __future__ import annotations

import json

import pytest

from intraday_bot import fundamentals_provider as fp


class _Response:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def test_fetch_fundamentals_normalizes_statistics(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_get(url: str, params: dict, timeout: int):
        calls.append((url, params))
        endpoint = url.rsplit("/", 1)[-1]
        if endpoint == "statistics":
            return _Response(
                {
                    "status": "ok",
                    "valuation": {"pe_ratio_ttm": "18.5"},
                    "profitability": {"return_on_equity": "16.2", "return_on_capital": "14.8"},
                    "financial_health": {"debt_to_equity": "0.72"},
                    "growth": {"eps_growth_yoy": "21.4", "net_income_growth": "17.9"},
                }
            )
        if endpoint == "earnings":
            return _Response({"earnings": []})
        return _Response({"sector": "Financial Services", "industry": "Asset Management"})

    monkeypatch.setenv("TWELVEDATA_API_KEY", "test-key")
    monkeypatch.setattr(fp.requests, "get", fake_get)

    result = fp.fetch_fundamentals("ABCAPITAL")

    assert result["symbol"] == "ABCAPITAL"
    assert result["pe"] == 18.5
    assert result["roe"] == 16.2
    assert result["roce"] == 14.8
    assert result["debt_to_equity"] == 0.72
    assert result["eps_growth"] == 21.4
    assert result["profit_growth"] == 17.9
    assert result["sector"] == "Financial Services"
    assert result["industry"] == "Asset Management"
    assert "relative_strength" not in result
    assert "market_trend" not in result
    assert result["missing_provider_fields"] == ["predictability", "earnings_quality"]
    assert calls[0][1]["exchange"] == "NSE"


def test_fetch_fundamentals_retries_qualified_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_get(url: str, params: dict, timeout: int):
        seen.append(str(params["symbol"]))
        if params["symbol"] == "ABCAPITAL":
            return _Response({"status": "error", "code": 400, "message": "symbol not found"})
        endpoint = url.rsplit("/", 1)[-1]
        if endpoint == "statistics":
            return _Response({"pe_ratio": 10})
        if endpoint == "earnings":
            return _Response({"earnings": []})
        return _Response({})

    monkeypatch.setenv("TWELVEDATA_API_KEY", "test-key")
    monkeypatch.setattr(fp.requests, "get", fake_get)

    result = fp.fetch_fundamentals("ABCAPITAL")

    assert result["pe"] == 10
    assert seen[:2] == ["ABCAPITAL", "NSE:ABCAPITAL"]
