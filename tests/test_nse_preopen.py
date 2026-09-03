from __future__ import annotations

import json

import pytest

from intraday_bot import nse_preopen


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_fetch_preopen_normalizes_iep_and_change(monkeypatch: pytest.MonkeyPatch) -> None:
    nse_preopen._cache = {"at": 0.0, "rows": [], "summary": {}}
    payload = {
        "data": [
            {
                "symbol": "ABCAPITAL",
                "previousClose": "200.00",
                "iep": "206.00",
                "change": "6.00",
                "pChange": "3.00",
                "finalQuantity": "120000",
                "totalBuyQuantity": "700000",
                "totalSellQuantity": "400000",
            }
        ]
    }
    monkeypatch.setattr(nse_preopen, "urlopen", lambda request, timeout=20: _Response(payload))

    rows = nse_preopen.fetch_preopen(force=True)

    assert rows[0]["symbol"] == "ABCAPITAL"
    assert rows[0]["prev_close"] == 200.0
    assert rows[0]["iep"] == 206.0
    assert rows[0]["change_pct"] == 3.0
    assert rows[0]["classification"] == "STRONG_POSITIVE"
    assert rows[0]["total_buy_qty"] == 700000.0


def test_market_context_builds_breadth_and_regime(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"symbol": "A", "change_pct": 2.0, "classification": "STRONG_POSITIVE"},
        {"symbol": "B", "change_pct": 1.0, "classification": "POSITIVE"},
        {"symbol": "C", "change_pct": -1.0, "classification": "NEGATIVE"},
    ]
    monkeypatch.setattr(nse_preopen, "fetch_preopen", lambda force=False, ttl_seconds=300: rows)

    result = nse_preopen.market_context()

    assert result["status"] == "AVAILABLE"
    assert result["breadth"]["advances"] == 2
    assert result["breadth"]["declines"] == 1
    assert result["breadth"]["unchanged"] == 0
    assert result["regime"] == "BULLISH_PREOPEN"


def test_stock_context_returns_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        nse_preopen,
        "fetch_preopen",
        lambda force=False, ttl_seconds=300: [
            {"symbol": "ABCAPITAL", "iep": 206.0, "change_pct": 3.0, "source": "NSE Pre-Open Market"}
        ],
    )
    result = nse_preopen.stock_context("ABCAPITAL")
    assert result["status"] == "AVAILABLE"
    assert result["symbol"] == "ABCAPITAL"
    assert result["iep"] == 206.0
