from __future__ import annotations

import json

from intraday_bot import fundamentals_cache as fc


def test_refresh_batch_persists_bounded_source_snapshot(tmp_path, monkeypatch):
    fc._LOCK = fc.threading.Lock()
    monkeypatch.setattr(fc, "CACHE_PATH", tmp_path / "fundamentals.json")
    monkeypatch.setenv("FUNDAMENTALS_REFRESH_PER_CYCLE", "1")
    monkeypatch.setenv("FUNDAMENTALS_REFRESH_SECONDS", "604800")

    calls: list[str] = []

    def fake_fetch(symbol: str, current_price=None):
        calls.append(symbol)
        return {
            "symbol": symbol,
            "source": "Twelve Data",
            "source_status": "AVAILABLE",
            "eps": 5.0,
            "profit_growth": 12.0,
        }

    monkeypatch.setattr(fc, "fetch_fundamentals", fake_fetch)

    cache = fc.refresh_batch([("ABCAPITAL", 123.0), ("RELIANCE", 2500.0)])

    assert calls == ["ABCAPITAL"]
    assert cache["ABCAPITAL"]["profit_growth"] == 12.0
    assert json.loads((tmp_path / "fundamentals.json").read_text())['ABCAPITAL']['symbol'] == 'ABCAPITAL'

    # A fresh cached value is reused; no second provider request is made.
    cache2 = fc.refresh_batch([("ABCAPITAL", 130.0)])
    assert calls == ["ABCAPITAL"]
    assert fc.get("ABCAPITAL", current_price=130.0)["pe"] == 26.0
    assert cache2["ABCAPITAL"]["eps"] == 5.0
