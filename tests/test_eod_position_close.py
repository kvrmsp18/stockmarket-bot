from __future__ import annotations

from intraday_bot.database import Database
from intraday_bot.runtime import _manage_positions


def test_eod_position_close_persists_trade_and_close_event_without_lock(tmp_path):
    db = Database(tmp_path / "trading.db")
    with db.connect() as con:
        con.execute(
            "INSERT INTO positions(position_id,symbol,mode,side,quantity,entry_price,current_price,stop,target,opened_at,payload) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                "POS-1", "TEST", "PAPER", "BUY", 10, 100.0, 100.0,
                95.0, 105.0, "2026-09-04T10:00:00+00:00", "{}",
            ),
        )

    _manage_positions(
        db,
        {"123": {"last_price": 106.0, "prev_close": 100.0, "volume": 100000}},
        [{"symbol": "TEST", "security_id": "123", "exchange_segment": "NSE_EQ"}],
    )

    assert db.scalar("SELECT COUNT(*) FROM positions WHERE closed_at IS NOT NULL") == 1
    assert db.scalar("SELECT COUNT(*) FROM trades WHERE symbol='TEST'") == 1
    assert db.scalar("SELECT COUNT(*) FROM events WHERE event_type='POSITION_CLOSED'") == 1
