"""Close all open simulated positions at the configured EOD square-off time.

This script uses the same intraday_bot ledger as the monitor. It never submits a
real broker order. Dhan is used only for the latest observable market prices so
paper positions can be reconciled before the daily report is produced.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from intraday_bot.alerts import telegram
from intraday_bot.brokers import DhanBroker
from intraday_bot.config import IST, settings
from intraday_bot.database import Database
from intraday_bot.runtime import _manage_positions, universe


def main() -> int:
    db = Database()
    uni = universe()
    if not uni:
        raise RuntimeError("DATA_UNAVAILABLE: NSE universe is empty")

    with db.connect() as con:
        open_positions = int(
            con.execute(
                "SELECT COUNT(*) FROM positions WHERE closed_at IS NULL AND mode IN ('PAPER','LIVE_TEST')"
            ).fetchone()[0]
        )

    if open_positions:
        broker = DhanBroker()
        try:
            qmap = broker.bulk_quotes(uni)
        except Exception as exc:
            db.event("eod", "ERROR", "EOD_MARKET_DATA_ERROR", {"error": str(exc)}, mode="PAPER")
            raise RuntimeError(f"EOD market data unavailable; open positions were NOT fabricated closed: {exc}") from exc
        _manage_positions(db, qmap, uni)

    now = datetime.now(IST)
    with db.connect() as con:
        remaining = int(
            con.execute(
                "SELECT COUNT(*) FROM positions WHERE closed_at IS NULL AND mode IN ('PAPER','LIVE_TEST')"
            ).fetchone()[0]
        )
        rows = con.execute(
            "SELECT symbol,side,quantity,entry_price,current_price,stop,target,closed_at,pnl "
            "FROM positions WHERE substr(opened_at,1,10)=? ORDER BY opened_at DESC",
            (now.date().isoformat(),),
        ).fetchall()

    if remaining:
        db.event(
            "eod",
            "WARN",
            "EOD_POSITIONS_REMAIN_OPEN",
            {"remaining_open_positions": remaining, "reason": "No valid current Dhan price was available for one or more paper positions"},
            mode="PAPER",
        )

    report_lines = [
        "📌 STOCKMARKET BOT — EOD CLOSE",
        f"Date: {now.strftime('%d-%b-%Y %H:%M:%S')} IST",
        f"Configured square-off: {settings.square_off_hour:02d}:{settings.square_off_minute:02d} IST",
        f"Positions before close: {open_positions}",
        f"Positions still open: {remaining}",
        "Mode: PAPER / simulated only",
    ]
    message = "\n".join(report_lines)
    print(message)
    if settings.telegram_token and settings.telegram_chat_id:
        telegram(message[:3900])

    # The dedicated daily report reads the same authoritative ledger and sends
    # the complete daily P&L separately.
    from scripts.eod_report import main as report_main
    return report_main()


if __name__ == "__main__":
    raise SystemExit(main())
