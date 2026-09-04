from __future__ import annotations

import json
import sqlite3

from intraday_bot.database import Database


def test_event_can_write_while_runtime_connection_is_open(tmp_path):
    db = Database(tmp_path / "trading.db")
    with db.connect() as con:
        con.execute(
            "INSERT INTO positions(position_id,symbol,mode,side,quantity,entry_price,current_price,stop,target,opened_at,payload) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("POS-1", "TEST", "PAPER", "BUY", 1, 100.0, 100.0, 95.0, 110.0, "2026-09-04T10:00:00+00:00", "{}"),
        )
        # This reproduces the old EOD pattern: a transaction is open on one
        # connection and the repository records the close event separately.
        db.event("execution", "INFO", "POSITION_CLOSED", {"trade_id": "TRD-1", "net_pnl": 5.0}, "TEST", "PAPER")

    assert db.scalar("SELECT COUNT(*) FROM events WHERE event_type='POSITION_CLOSED'") == 1
    assert db.scalar("SELECT COUNT(*) FROM positions WHERE position_id='POS-1'") == 1
