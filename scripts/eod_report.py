from __future__ import annotations

import json
from datetime import datetime, time as clock, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

from intraday_bot.alerts import telegram
from intraday_bot.brokers import DhanBroker
from intraday_bot.config import settings
from intraday_bot.database import Database
from intraday_bot.runtime import quote, universe

IST = ZoneInfo("Asia/Kolkata")
EXECUTION_REJECTIONS = {
    "DUPLICATE_ORDER",
    "ENTRY_EXPIRED",
    "POSITION_LIMIT",
    "DAILY_LOSS_LIMIT",
    "CAPITAL_DEPLOYMENT_LIMIT",
    "SECTOR_EXPOSURE_LIMIT",
    "INSUFFICIENT_FUNDS",
}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _charges(turnover: float) -> float:
    brokerage = min(40.0, turnover * 0.0003)
    stt = turnover * 0.00025
    exchange = turnover * 0.0000307
    sebi = turnover / 10_000_000 * 10
    stamp = turnover * 0.00003
    gst = (brokerage + exchange + sebi) * 0.18
    return brokerage + stt + exchange + sebi + stamp + gst


def _morning_window_bounds(today: str) -> tuple[str, str]:
    day = datetime.strptime(today, "%Y-%m-%d").date()
    start = datetime.combine(day, clock(9, 15), tzinfo=IST).astimezone(ZoneInfo("UTC"))
    end = datetime.combine(day, clock(11, 0), tzinfo=IST).astimezone(ZoneInfo("UTC"))
    return start.isoformat(), end.isoformat()


def _in_morning_window(ts: str) -> bool:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(IST)
    except ValueError:
        return False
    return clock(9, 15) <= dt.time() <= clock(11, 0)


def _load_imported_morning(today: str) -> list[dict[str, Any]]:
    path = Path("data/morning_recommendations") / f"{today}.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = payload.get("records") if isinstance(payload, dict) else None
    return [dict(x) for x in rows if isinstance(x, dict)] if isinstance(rows, list) else []


def _load_bot_morning_recommendations(db: Database, today: str) -> list[dict[str, Any]]:
    """Recover every actionable morning idea from persistent state.

    The final cycle candidate list contains only execution survivors. Candidates
    rejected by execution gates are persisted as SIGNAL_REJECTED events. EOD
    combines both ledgers so a later monitor_status overwrite cannot erase a
    morning recommendation merely because it was rejected before paper entry.
    """
    rows_out: list[dict[str, Any]] = []
    previous_day = (datetime.strptime(today, "%Y-%m-%d").date() - timedelta(days=1)).isoformat()
    with db.connect() as con:
        cycle_rows = con.execute(
            "SELECT cycle_id,started_at,ended_at,payload FROM cycles "
            "WHERE substr(started_at,1,10) IN (?, ?) ORDER BY started_at",
            (today, previous_day),
        ).fetchall()
        event_rows = con.execute(
            "SELECT id,ts,symbol,mode,payload FROM events "
            "WHERE event_type='SIGNAL_REJECTED' AND ts>=? AND ts<=? ORDER BY id",
            _morning_window_bounds(today),
        ).fetchall()

    seen: set[tuple[str, str]] = set()

    for row in cycle_rows:
        started = str(row["started_at"] or "")
        try:
            dt = datetime.fromisoformat(started.replace("Z", "+00:00")).astimezone(IST)
        except ValueError:
            continue
        if not (clock(9, 15) <= dt.time() <= clock(11, 0)):
            continue
        try:
            payload = json.loads(row["payload"] or "{}")
        except Exception:
            continue
        for candidate in payload.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            decision = str(candidate.get("decision", "")).upper()
            symbol = str(candidate.get("symbol", "")).upper()
            if decision not in {"BUY", "SELL"} or not symbol:
                continue
            key = (symbol, decision)
            if key in seen:
                continue
            item = dict(candidate)
            item["source"] = "BOT_CYCLE_LEDGER"
            item["cycle_id"] = row["cycle_id"]
            item["generated_at"] = started
            rows_out.append(item)
            seen.add(key)

    for row in event_rows:
        ts = str(row["ts"] or "")
        if not _in_morning_window(ts):
            continue
        try:
            payload = json.loads(row["payload"] or "{}")
        except Exception:
            continue
        if not isinstance(payload, dict) or payload.get("record_type") != "REJECTED_SIGNAL":
            continue
        decision = str(payload.get("decision", "")).upper()
        symbol = str(payload.get("symbol") or row["symbol"] or "").upper()
        rejection = str(payload.get("rejection_reason", "")).upper()
        if decision not in {"BUY", "SELL"} or not symbol or rejection not in EXECUTION_REJECTIONS:
            continue
        key = (symbol, decision)
        if key in seen:
            continue
        item = dict(payload)
        item["symbol"] = symbol
        item["decision"] = decision
        item["source"] = "BOT_EXECUTION_REJECTION_LEDGER"
        item["generated_at"] = ts
        item["event_id"] = row["id"]
        item["execution_rejection_reason"] = rejection
        item["paper_execution_status"] = "REJECTED_BEFORE_PAPER_ENTRY"
        rows_out.append(item)
        seen.add(key)

    rows_out.sort(key=lambda x: str(x.get("generated_at", "")))
    return rows_out


def _load_paper_trades(db: Database, today: str) -> list[dict[str, Any]]:
    with db.connect() as con:
        rows = con.execute(
            "SELECT * FROM trades WHERE mode IN ('PAPER','LIVE_TEST') "
            "AND closed_at IS NOT NULL AND substr(closed_at,1,10)=? ORDER BY closed_at",
            (today,),
        ).fetchall()
    return [dict(row) for row in rows]


def _load_open_positions(db: Database) -> int:
    with db.connect() as con:
        return int(con.execute(
            "SELECT COUNT(*) FROM positions WHERE closed_at IS NULL AND mode IN ('PAPER','LIVE_TEST')"
        ).fetchone()[0])


def _eod_quotes(symbols: list[str]) -> tuple[dict[str, float], str | None]:
    wanted = {str(x).upper() for x in symbols if str(x).strip()}
    if not wanted:
        return {}, None
    by_symbol = {str(x.get("symbol", "")).upper(): x for x in universe()}
    instruments = []
    for symbol in sorted(wanted):
        item = by_symbol.get(symbol)
        if not item or not item.get("security_id"):
            continue
        instruments.append({
            "symbol": symbol,
            "security_id": item["security_id"],
            "exchange_segment": item.get("exchange_segment", "NSE_EQ"),
        })
    if not instruments:
        return {}, "No security IDs found for morning recommendation symbols."
    try:
        qmap = DhanBroker().bulk_quotes(instruments)
    except Exception as exc:
        return {}, str(exc)
    prices: dict[str, float] = {}
    for instrument in instruments:
        q = qmap.get(str(instrument["security_id"]), {})
        price, _, _ = quote(q)
        if price > 0:
            prices[instrument["symbol"]] = price
    return prices, None if prices else "Dhan returned no usable EOD prices for the recommendation symbols."


def _evaluate_reference(row: dict[str, Any], eod_price: float | None) -> dict[str, Any]:
    decision = str(row.get("decision", "")).upper()
    entry = _f(row.get("entry"))
    qty = _i(row.get("quantity"))
    result = dict(row)
    result["eod_price"] = eod_price
    result["evaluation"] = "EOD_PRICE_UNAVAILABLE"
    result["hypothetical_gross_pnl"] = None
    result["hypothetical_charges"] = None
    result["hypothetical_net_pnl"] = None
    result["pnl_type"] = "HYPOTHETICAL_REFERENCE_ENTRY"
    if not eod_price or entry <= 0 or qty <= 0 or decision not in {"BUY", "SELL"}:
        return result
    gross = (eod_price - entry) * qty * (1 if decision == "BUY" else -1)
    charges = _charges((eod_price + entry) * qty)
    net = gross - charges
    stop = _f(row.get("stop"))
    target = _f(row.get("target"))
    if decision == "BUY":
        if eod_price <= stop:
            outcome = "BELOW_STOP"
        elif eod_price >= target:
            outcome = "AT_OR_ABOVE_TARGET"
        elif eod_price > entry:
            outcome = "EOD_GAIN"
        elif eod_price < entry:
            outcome = "EOD_LOSS"
        else:
            outcome = "EOD_FLAT"
    else:
        if eod_price >= stop:
            outcome = "ABOVE_STOP"
        elif eod_price <= target:
            outcome = "AT_OR_BELOW_TARGET"
        elif eod_price < entry:
            outcome = "EOD_GAIN"
        elif eod_price > entry:
            outcome = "EOD_LOSS"
        else:
            outcome = "EOD_FLAT"
    result.update({
        "evaluation": outcome,
        "hypothetical_gross_pnl": round(gross, 2),
        "hypothetical_charges": round(charges, 2),
        "hypothetical_net_pnl": round(net, 2),
    })
    return result


def _reconcile_bot_recommendations(rows: list[dict[str, Any]], trades: list[dict[str, Any]], eod_prices: dict[str, float]) -> list[dict[str, Any]]:
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        by_symbol.setdefault(str(trade.get("symbol", "")).upper(), []).append(trade)
    out = []
    for row in rows:
        item = dict(row)
        rejected_before_entry = item.get("source") == "BOT_EXECUTION_REJECTION_LEDGER"
        matches = [] if rejected_before_entry else by_symbol.get(str(row.get("symbol", "")).upper(), [])
        if matches:
            trade = matches[0]
            item["paper_execution_status"] = "EXECUTED_PAPER"
            item["paper_trade_id"] = trade.get("trade_id")
            item["realized_net_pnl"] = _f(trade.get("net_pnl"))
            item["pnl_type"] = "REALIZED_PAPER_TRADE"
            item["eod_price"] = _f(trade.get("exit_price"), 0.0)
        elif not rejected_before_entry:
            item["paper_execution_status"] = "NO_PAPER_TRADE"
            item["realized_net_pnl"] = None
            item["pnl_type"] = "HYPOTHETICAL_REFERENCE_ENTRY"
        else:
            item["realized_net_pnl"] = None
            item["pnl_type"] = "HYPOTHETICAL_REFERENCE_ENTRY"
        if item.get("paper_execution_status") != "EXECUTED_PAPER":
            item = _evaluate_reference(item, eod_prices.get(str(item.get("symbol", "")).upper()))
        out.append(item)
    return out


def main() -> int:
    db = Database()
    today = datetime.now(IST).date().isoformat()
    trades = _load_paper_trades(db, today)
    open_positions = _load_open_positions(db)
    imported = _load_imported_morning(today)
    bot_morning = _load_bot_morning_recommendations(db, today)

    imported_symbols = {str(x.get("symbol", "")).upper() for x in imported}
    bot_morning = [x for x in bot_morning if str(x.get("symbol", "")).upper() not in imported_symbols]

    all_morning_symbols = list(imported_symbols | {str(x.get("symbol", "")).upper() for x in bot_morning})
    eod_prices, quote_error = _eod_quotes(all_morning_symbols)
    manual_rows = [_evaluate_reference(x, eod_prices.get(str(x.get("symbol", "")).upper())) for x in imported]
    bot_rows = _reconcile_bot_recommendations(bot_morning, trades, eod_prices)

    signals_today = int(db.scalar("SELECT COUNT(*) FROM signals WHERE substr(ts,1,10)=?", (today,)) or 0)
    net = sum(_f(x.get("net_pnl")) for x in trades)
    gross = sum(_f(x.get("gross_pnl")) for x in trades)
    charges = sum(_f(x.get("charges")) for x in trades)
    wins = sum(1 for x in trades if _f(x.get("net_pnl")) > 0)
    losses = sum(1 for x in trades if _f(x.get("net_pnl")) < 0)
    gross_profit = sum(max(0.0, _f(x.get("net_pnl"))) for x in trades)
    gross_loss = sum(min(0.0, _f(x.get("net_pnl"))) for x in trades)
    profit_factor = gross_profit / abs(gross_loss) if gross_loss else None

    manual_wins = sum(1 for x in manual_rows if _f(x.get("hypothetical_net_pnl"), 0) > 0)
    manual_losses = sum(1 for x in manual_rows if _f(x.get("hypothetical_net_pnl"), 0) < 0)
    manual_net = sum(_f(x.get("hypothetical_net_pnl"), 0) for x in manual_rows)
    manual_price_count = sum(1 for x in manual_rows if x.get("eod_price"))

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
        "gross_pnl": round(gross, 2),
        "charges": round(charges, 2),
        "net_pnl": round(net, 2),
        "win_rate": wins / len(trades) if trades else 0.0,
        "profit_factor": profit_factor,
        "open_positions": open_positions,
        "morning_recommendations": {
            "imported_user_morning_count": len(manual_rows),
            "bot_morning_count": len(bot_rows),
            "total_count": len(manual_rows) + len(bot_rows),
            "manual_purchase_status": "USER_REPORTED_MANUAL_PURCHASE" if manual_rows else "NOT_RECORDED",
            "eod_prices_available": manual_price_count,
            "hypothetical_reference_net_pnl": round(manual_net, 2),
            "hypothetical_wins": manual_wins,
            "hypothetical_losses": manual_losses,
            "records": manual_rows + bot_rows,
        },
        "eod_quote_error": quote_error,
        "data_integrity": {
            "manual_purchase_prices_present": False,
            "manual_purchase_pnl_claimed": False,
            "paper_trades_separated_from_manual_purchases": True,
            "hypothetical_pnl_is_not_realized_pnl": True,
            "execution_rejected_morning_candidates_recovered": True,
        },
    }

    out = Path("reports/daily")
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{today}.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    lines = [
        "📊 STOCKMARKET BOT — EOD PAPER REPORT",
        f"Date: {datetime.now(IST).strftime('%d-%b-%Y %H:%M:%S')} IST",
        "Mode: PAPER (simulated orders only)",
        f"Signals: {signals_today} | Paper trades: {len(trades)} | Wins: {wins} | Losses: {losses}",
        f"Realized paper NET P&L: ₹{net:,.2f}",
        f"Starting paper capital: ₹{settings.reference_capital:,.2f}",
        f"Ending paper capital: ₹{settings.reference_capital + net:,.2f}",
        f"Open positions: {open_positions}",
    ]
    if manual_rows:
        lines.append(f"🌅 Morning recommendations recorded: {len(manual_rows)} (user-reported manual purchases)")
        lines.append("Reference-entry EOD comparison — NOT realized manual P&L:")
        for row in manual_rows:
            symbol = str(row.get("symbol", "?"))
            price = row.get("eod_price")
            outcome = row.get("evaluation", "EOD_PRICE_UNAVAILABLE")
            hyp = row.get("hypothetical_net_pnl")
            if price:
                lines.append(f"{symbol}: EOD ₹{_f(price):.2f} | {outcome} | hypothetical net ₹{_f(hyp):,.2f}")
            else:
                lines.append(f"{symbol}: EOD price unavailable | {outcome}")
    if bot_rows:
        lines.append(f"Bot morning recommendations recovered: {len(bot_rows)}")
        for row in bot_rows[:8]:
            lines.append(f"{row.get('decision','?')} {row.get('symbol','?')}: {row.get('paper_execution_status','UNKNOWN')} | reason={row.get('execution_rejection_reason', row.get('rejection_reason', '—'))} | P&L type={row.get('pnl_type','UNKNOWN')}")
    if quote_error:
        lines.append(f"EOD quote warning: {quote_error[:220]}")
    lines.append(f"Profit factor: {'N/A' if profit_factor is None else f'{profit_factor:.2f}'}")
    lines.append(f"Report: {path}")
    message = "\n".join(lines)
    print(message)
    if settings.telegram_token and settings.telegram_chat_id:
        if telegram(message[:3900]):
            print("Telegram EOD report delivered.")
        else:
            print("Telegram EOD report could not be delivered; report saved to GitHub.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
