from __future__ import annotations

import intraday_bot.sector_intelligence as si


def test_sector_membership_prefers_specific_sector(monkeypatch, tmp_path):
    payload = {
        "status": "AVAILABLE",
        "source": "TEST",
        "fetched_at": "2026-09-04T00:00:00+00:00",
        "sectors": {
            "BANKING": ["HDFCBANK", "ICICIBANK"],
            "PRIVATE_BANK": ["HDFCBANK"],
            "PSU_BANK": ["SBIN"],
        },
        "symbol_sector": {
            "HDFCBANK": "BANKING",
            "ICICIBANK": "BANKING",
            "SBIN": "PSU_BANK",
        },
        "symbol_sources": {
            "HDFCBANK": ["BANKING", "PRIVATE_BANK"],
            "ICICIBANK": ["BANKING"],
            "SBIN": ["PSU_BANK"],
        },
    }
    monkeypatch.setattr(si, "membership", lambda force_refresh=False: payload)
    monkeypatch.setattr(si, "STATS_PATH", tmp_path / "sector_intelligence.json")

    universe = [
        {"symbol": "HDFCBANK", "security_id": "1"},
        {"symbol": "ICICIBANK", "security_id": "2"},
        {"symbol": "SBIN", "security_id": "3"},
    ]
    qmap = {
        "1": {"last_price": 105.0, "prev_close": 100.0},
        "2": {"last_price": 98.0, "prev_close": 100.0},
        "3": {"last_price": 101.0, "prev_close": 100.0},
    }

    result = si.build(universe, qmap)
    assert result["status"] == "AVAILABLE"
    assert result["classified_symbols"] == 3
    assert result["unclassified_universe_symbols"] == 0
    assert result["sectors"]["BANKING"]["advancing"] == 1
    assert result["sectors"]["BANKING"]["declining"] == 1
    assert result["sectors"]["PSU_BANK"]["advancing"] == 1
    assert (tmp_path / "sector_intelligence.json").exists()


def test_sector_build_does_not_fabricate_missing_quotes(monkeypatch, tmp_path):
    payload = {
        "status": "AVAILABLE",
        "source": "TEST",
        "fetched_at": "2026-09-04T00:00:00+00:00",
        "sectors": {"BANKING": ["HDFCBANK"]},
        "symbol_sector": {"HDFCBANK": "BANKING"},
        "symbol_sources": {"HDFCBANK": ["BANKING"]},
    }
    monkeypatch.setattr(si, "membership", lambda force_refresh=False: payload)
    monkeypatch.setattr(si, "STATS_PATH", tmp_path / "sector_intelligence.json")

    result = si.build(
        [{"symbol": "HDFCBANK", "security_id": "1"}],
        {"1": {}},
    )
    banking = result["sectors"]["BANKING"]
    assert banking["quoted"] == 0
    assert banking["advancing"] == 0
    assert banking["declining"] == 0
    assert banking["average_change_pct"] is None
