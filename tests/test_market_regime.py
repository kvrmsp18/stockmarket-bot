from __future__ import annotations

import pandas as pd
import pytest

from intraday_bot.brokers import DhanBroker
from intraday_bot.market_regime import _analyse_index, _normalise, build


def _daily_frame(n: int = 320) -> pd.DataFrame:
    dates = pd.date_range("2025-07-01", periods=n, freq="B", tz="UTC")
    close = pd.Series(range(100, 100 + n), dtype=float)
    return pd.DataFrame(
        {
            "timestamp": dates,
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 1000,
        }
    )


def test_normalise_handles_index_aliases() -> None:
    assert _normalise("NIFTY_50") == "NIFTY 50"
    assert _normalise("  nifty   bank ") == "NIFTY BANK"


def test_daily_history_payload_is_valid_for_dhan_index() -> None:
    payload = DhanBroker._daily_history_payload(
        "13",
        "NSE_IDX",
        "INDEX",
        "2025-07-01",
        "2026-09-04",
    )
    assert payload == {
        "securityId": "13",
        "exchangeSegment": "IDX_I",
        "instrument": "INDEX",
        "fromDate": "2025-07-01",
        "toDate": "2026-09-04",
        "expiryCode": 0,
        "oi": False,
    }


def test_analyse_index_requires_sufficient_history() -> None:
    with pytest.raises(RuntimeError, match="INDEX_DATA_INSUFFICIENT:NIFTY 50:49"):
        _analyse_index("NIFTY 50", _daily_frame(49))


def test_build_uses_resolved_index_ids_and_produces_combined_regime(monkeypatch, tmp_path) -> None:
    resolved = {
        "NIFTY_50": {"security_id": "1", "symbol": "NIFTY 50"},
        "BANK_NIFTY": {"security_id": "2", "symbol": "BANK NIFTY"},
    }
    calls: list[tuple[str, str, str]] = []

    monkeypatch.setattr("intraday_bot.market_regime._resolve_indices", lambda: resolved)

    class Broker:
        def daily_history(self, security_id: str, exchange_segment: str, instrument: str):
            calls.append((security_id, exchange_segment, instrument))
            frame = _daily_frame()
            if security_id == "2":
                frame["close"] = frame["close"] * 1.001
            return frame

    result = build(Broker(), str(tmp_path / "market_regime.json"))

    assert result["status"] == "AVAILABLE"
    assert result["combined_regime"] in {"BULLISH", "BEARISH", "MIXED"}
    assert calls == [("1", "IDX_I", "INDEX"), ("2", "IDX_I", "INDEX")]
    assert (tmp_path / "market_regime.json").exists()
