from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from intraday_bot.alerts import telegram
from intraday_bot.config import settings
from intraday_bot.database import Database

IST = ZoneInfo("Asia/Kolkata")


def main() -> int:
    db = Database()
    today = datetime.now(IST).date().isoformat()
    with db.connect() as con:
        rows = con.execute(
            "SELECT * FROM trades WHERE mode IN ('PAPER','LIVE_TEST') "
            "AND closed_at IS NOT NULL AND substr(closed_at,1,10)=? ORDER BY closed_at",
            (today,),
        ).fetchall()
        trades = [dict(row) for row in rows]
        open_positions = int(
            con.execute(
                "SELECT COUNT(*) FROM positions WHERE closed_at IS NULL AND mode IN ('PAPER','LIVE_TEST')"
            ).fetchone()[0]
        )
        signals_today = int(
            con.execute(
                "SELECT COUNT(*) FROM signals WHERE substr(ts,1,10)=?",
                (today,),
            ).fetchone()[0]
        )

    net = sum(float(x.get("net_pnl", 0) or 0) for x in trades)
    gross = sum(float(x.get("gross_pnl", 0) or 0) for x in trades)
    charges = sum(float(x.get("charges", 0) or 0) for x in trades)
    wins = sum(1 for x in trades if float(x.get("net_pnl", 0) or 0) > 0)
    losses = sum(1 for x in trades if float(x.get("net_pnl", 0) or 0) < 0)
    gross_profit = sum(max(0.0, float(x.get("net_pnl", 0) or 0)) for x in trades)
    gross_loss = sum(min(0.0, float(x.get("net_pnl", 0) or 0)) for x in trades)
    profit_factor = gross_profit / abs(gross_loss) if gross_loss else None

    report = {
        "date": today,
        "mode": "PAPER",
        "starting_reference_capital": settings.reference_capital,
        "ending_reference_capital": settings.reference_capital + net,
        "signals_today": signals_today,
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "breakeven": len(trades) - wins - losses,
        "gross_pnl": gross,
        "charges": charges,
        "net_pnl": net,
        "win_rate": wins / len(trades) if trades else 0.0,
        "profit_factor": profit_factor,
        "open_positions": open_positions,
    }

    out = Path("reports/daily")
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{today}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    message = (
        "📊 STOCKMARKET BOT — EOD PAPER REPORT\n"
        f"Date: {datetime.now(IST).strftime('%d-%b-%Y')}\n"
        "Mode: PAPER (simulated orders only)\n"
        f"Signals: {signals_today} | Trades: {len(trades)} | Wins: {wins} | Losses: {losses}\n"
        f"Gross P&L: ₹{gross:,.2f} | Charges: ₹{charges:,.2f}\n"
        f"NET P&L: ₹{net:,.2f}\n"
        f"Starting paper capital: ₹{settings.reference_capital:,.2f}\n"
        f"Ending paper capital: ₹{settings.reference_capital + net:,.2f}\n"
        f"Profit factor: {'N/A' if profit_factor is None else f'{profit_factor:.2f}'}\n"
        f"Open positions: {open_positions}\n"
        f"Report: {path}"
    )
    print(message)
    if settings.telegram_token and settings.telegram_chat_id:
        if telegram(message[:3900]):
            print("Telegram EOD report delivered.")
        else:
            print("Telegram EOD report could not be delivered; report saved to GitHub.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
