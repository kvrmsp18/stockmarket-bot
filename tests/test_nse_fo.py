from __future__ import annotations

import pytest

from intraday_bot import nse_fo


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        import json
        return json.dumps(self.payload).encode("utf-8")


def test_fetch_oi_spurts_normalizes_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    nse_fo._cache = {"at": 0.0, "rows": []}

    payload = {
        "data": [
            {
                "symbol": "ABCAPITAL",
                "ltp": "123.45",
                "pChange": "2.5",
                "pChangeInOI": "18.0",
                "changeinOpenInterest": "900000",
                "openInterest": "5900000",
                "volume": "1200000",
                "value": "148000000.00",
            }
        ]
    }

    monkeypatch.setattr(nse_fo, "urlopen", lambda request, timeout=20: _Response(payload))
    rows = nse_fo.fetch_oi_spurts(force=True)

    assert rows == [
        {
            "symbol": "ABCAPITAL",
            "ltp": 123.45,
            "change_pct": 2.5,
            "oi_change_pct": 18.0,
            "oi_change": 900000.0,
            "open_interest": 5900000.0,
            "volume": 1200000.0,
            "value": 148000000.0,
            "timestamp": None,
            "source": "NSE OI Spurts",
        }
    ]


def test_oi_context_classifies_long_buildup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        nse_fo,
        "fetch_oi_spurts",
        lambda force=False, ttl_seconds=300: [
            {
                "symbol": "ABCAPITAL",
                "change_pct": 2.0,
                "oi_change_pct": 10.0,
                "oi_change": 1000.0,
                "open_interest": 5000.0,
                "volume": 10000.0,
                "value": 1000000.0,
                "ltp": 100.0,
                "timestamp": None,
                "source": "NSE OI Spurts",
            }
        ],
    )
    result = nse_fo.oi_context("ABCAPITAL")
    assert result["status"] == "AVAILABLE"
    assert result["signal"] == "LONG_BUILDUP"
