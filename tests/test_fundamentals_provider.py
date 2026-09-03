from __future__ import annotations

import pytest

from intraday_bot import fundamentals_provider as fp


class _Response:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def test_fetch_fundamentals_uses_statements_not_paid_statistics(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_get(url: str, params: dict, timeout: int):
        calls.append((url, params.copy()))
        endpoint = url.rsplit("/", 1)[-1]
        if endpoint == "income_statement":
            return _Response({
                "data": [
                    {"fiscal_date": "2026-03-31", "net_income": 200, "operating_income": 300, "diluted_eps": 4.0},
                    {"fiscal_date": "2025-03-31", "net_income": 160, "operating_income": 240, "diluted_eps": 3.2},
                    {"fiscal_date": "2024-03-31", "net_income": 120, "operating_income": 180, "diluted_eps": 2.4},
                ]
            })
        if endpoint == "balance_sheet":
            return _Response({
                "data": [
                    {"fiscal_date": "2026-03-31", "total_equity": 1000, "total_debt": 200, "cash_and_cash_equivalents": 100},
                    {"fiscal_date": "2025-03-31", "total_equity": 800, "total_debt": 240, "cash_and_cash_equivalents": 80},
                ]
            })
        if endpoint == "cash_flow":
            return _Response({"data": [{"fiscal_date": "2026-03-31", "operating_cash_flow": 220}]})
        if endpoint == "earnings":
            return _Response({"earnings": [{"fiscal_date": "2026-03-31", "eps": 4.0}, {"fiscal_date": "2025-03-31", "eps": 3.2}]})
        if endpoint == "profile":
            return _Response({"name": "ABC Capital", "sector": "Financial Services", "industry": "Asset Management"})
        if endpoint == "quote":
            return _Response({"close": 80})
        raise AssertionError(endpoint)

    monkeypatch.setenv("TWELVEDATA_API_KEY", "test-key")
    monkeypatch.setattr(fp.requests, "get", fake_get)

    result = fp.fetch_fundamentals("ABCAPITAL")

    assert result["symbol"] == "ABCAPITAL"
    assert result["eps"] == 4.0
    assert result["profit"] == 200.0
    assert result["profit_growth"] == pytest.approx(25.0)
    assert result["eps_growth"] == pytest.approx(25.0)
    assert result["debt_to_equity"] == pytest.approx(0.2)
    assert result["roe"] == pytest.approx(22.2222, rel=1e-3)
    assert result["roce"] == pytest.approx(27.2727, rel=1e-3)
    assert result["earnings_quality"] == pytest.approx(1.1)
    assert result["pe"] == pytest.approx(20.0)
    assert result["current_price"] == 80.0
    assert result["predictability"] is not None
    assert "statistics" not in {url.rsplit("/", 1)[-1] for url, _ in calls}
    assert all(params["exchange"] == "NSE" for _, params in calls if params["symbol"] == "ABCAPITAL")


def test_fetch_fundamentals_reports_unavailable_endpoints_without_fabrication(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, params: dict, timeout: int):
        endpoint = url.rsplit("/", 1)[-1]
        if endpoint == "income_statement":
            return _Response({"data": [{"fiscal_date": "2026-03-31", "net_income": 100, "diluted_eps": 2.0}]})
        if endpoint == "balance_sheet":
            return _Response({"data": [{"fiscal_date": "2026-03-31", "total_equity": 500, "total_debt": 100, "cash_and_cash_equivalents": 50}]})
        if endpoint == "cash_flow":
            return _Response({"data": [{"fiscal_date": "2026-03-31", "operating_cash_flow": 90}]})
        return _Response({"code": 403, "message": "endpoint unavailable"}, status_code=403)

    monkeypatch.setenv("TWELVEDATA_API_KEY", "test-key")
    monkeypatch.setattr(fp.requests, "get", fake_get)

    result = fp.fetch_fundamentals("ABCAPITAL")

    assert result["profit"] == 100.0
    assert result["debt_to_equity"] == pytest.approx(0.2)
    assert "pe" not in result
    assert "eps_growth" not in result
    assert "pe" in result["missing_provider_fields"]
    assert result["endpoint_errors"]


def test_fetch_fundamentals_retries_qualified_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_get(url: str, params: dict, timeout: int):
        seen.append(str(params["symbol"]))
        endpoint = url.rsplit("/", 1)[-1]
        if params["symbol"] == "ABCAPITAL":
            return _Response({"status": "error", "code": 400, "message": "symbol not found"})
        if endpoint == "income_statement":
            return _Response({"data": [{"fiscal_date": "2026-03-31", "net_income": 50, "diluted_eps": 1.0}]})
        if endpoint == "balance_sheet":
            return _Response({"data": [{"fiscal_date": "2026-03-31", "total_equity": 250, "total_debt": 50}]})
        if endpoint == "cash_flow":
            return _Response({"data": [{"fiscal_date": "2026-03-31", "operating_cash_flow": 55}]})
        if endpoint == "earnings":
            return _Response({"earnings": []})
        return _Response({})

    monkeypatch.setenv("TWELVEDATA_API_KEY", "test-key")
    monkeypatch.setattr(fp.requests, "get", fake_get)

    result = fp.fetch_fundamentals("ABCAPITAL")

    assert result["profit"] == 50.0
    assert seen[0] == "ABCAPITAL"
    assert "NSE:ABCAPITAL" in seen
