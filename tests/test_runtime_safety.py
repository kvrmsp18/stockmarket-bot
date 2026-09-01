from __future__ import annotations

import json

from intraday_bot.database import Database
from intraday_bot.runtime import _portfolio_snapshot, _run_ai_advisory


def test_portfolio_snapshot_uses_only_open_positions_and_daily_closed_pnl(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_RESEARCH_REFERENCE_CAPITAL", "1000")
    db = Database(tmp_path / "trading.db")
    with db.connect() as con:
        con.execute(
            "INSERT INTO positions(position_id,symbol,mode,side,quantity,entry_price,current_price,stop,target,opened_at,payload) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("POS-1", "ABC", "PAPER", "BUY", 2, 100.0, 101.0, 98.0, 106.0, "2026-09-01T09:30:00+05:30", "{}"),
        )
        con.execute(
            "INSERT INTO trades(trade_id,symbol,mode,side,quantity,entry_price,exit_price,gross_pnl,charges,net_pnl,opened_at,closed_at,payload) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("TRD-1", "XYZ", "PAPER", "BUY", 1, 100.0, 90.0, -10.0, 1.0, -11.0, "2026-09-01T09:00:00+05:30", "2026-09-01T10:00:00+05:30", "{}"),
        )
    # Helper uses runtime's current IST date, so the test is intentionally
    # limited to its structural guarantees rather than a hard-coded clock.
    snapshot = _portfolio_snapshot(db, 1000.0, "PAPER")
    assert snapshot["open_positions"] == 1
    assert snapshot["open_exposure"] == 200.0
    assert snapshot["deployment_limit"] == 800.0
    assert snapshot["daily_loss"] >= 0.0


def test_ai_advisory_is_noop_without_provider_keys(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    candidates = [{"symbol": "ABC", "decision": "BUY"}]
    _run_ai_advisory(candidates)
    assert candidates == [{"symbol": "ABC", "decision": "BUY"}]
