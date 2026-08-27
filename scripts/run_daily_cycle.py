"""Run one safe paper-trading monitor cycle.

The cycle scans the configured universe, freezes newly actionable paper
signals, records a machine-readable health snapshot for the dashboard, and
sends a Telegram heartbeat with funds/scan/candidate information when Telegram
is configured. No broker order is placed by this script.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from src.dhan_api import DhanAPIError, DhanAuthenticationError, DhanHQClient
from src.monitor_service import run_stock_monitor
from src.paper_trading_validation import PaperSignal
from src.stock_monitor import StockMonitorSnapshot
from src.telegram_notify import TelegramNotifier
from src.validation_store import ValidationStore

DEFAULT_JOURNAL_PATH = "data/paper_trading_journal.jsonl"
DEFAULT_STATUS_PATH = "data/monitor_status.json"
IST = ZoneInfo("Asia/Kolkata")


def _reference_capital() -> float:
    raw = os.getenv("BOT_RESEARCH_REFERENCE_CAPITAL", "100000").strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 100000.0
    return value if value > 0 else 100000.0


def _resolve_account_equity(notifier: TelegramNotifier) -> tuple[float, float | None, str, str | None]:
    """Prefer real Dhan available funds; retain a safe paper-trading fallback."""
    fallback = _reference_capital()
    client = DhanHQClient(timeout=10.0)
    if not client.configured:
        return fallback, None, "reference_capital", "Dhan credentials are not configured."

    try:
        available = client.available_funds(client.fund_limits())
        if available > 0:
            return available, available, "dhan_fundlimit", None
        return fallback, available, "reference_capital_dhan_zero", "Dhan reported zero available funds; paper scan uses reference capital."
    except DhanAuthenticationError as exc:
        detail = str(exc)
        try:
            notifier.authentication_problem(detail)
        except Exception as notify_exc:
            print(f"Telegram authentication alert failed: {notify_exc}", file=sys.stderr)
        return fallback, None, "reference_capital_auth_failed", detail
    except DhanAPIError as exc:
        return fallback, None, "reference_capital_dhan_error", str(exc)
    except Exception as exc:
        return fallback, None, "reference_capital_fund_error", str(exc)


def _symbols_with_open_signal_today(store: ValidationStore, *, today: date) -> set[str]:
    resolved_ids = {record["payload"]["signal_id"] for record in store.outcomes()}
    open_symbols: set[str] = set()
    for record in store.signals():
        payload = record["payload"]
        if payload["signal_id"] in resolved_ids:
            continue
        generated_at = datetime.fromisoformat(payload["generated_at"])
        if generated_at.date() == today:
            open_symbols.add(payload["symbol"])
    return open_symbols


def signals_from_snapshot(
    snapshot: StockMonitorSnapshot,
    *,
    generated_at: datetime,
    already_open: set[str] = frozenset(),
) -> list[PaperSignal]:
    """Freeze at most one paper signal per symbol per day."""
    signals: list[PaperSignal] = []
    for row in snapshot.rows:
        if row.symbol in already_open:
            continue
        signals.append(
            PaperSignal(
                signal_id=f"{row.symbol}-{generated_at:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}",
                symbol=row.symbol,
                direction=row.direction,
                generated_at=generated_at,
                entry=row.entry,
                stop_loss=row.stop_loss,
                target=row.target,
                quantity=row.ai_quantity,
                confidence=row.confidence,
                risk_reward=row.risk_reward,
            )
        )
    return signals


def _write_status(payload: dict) -> None:
    path = Path(os.getenv("MONITOR_STATUS_PATH", DEFAULT_STATUS_PATH))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _candidate_payload(snapshot: StockMonitorSnapshot) -> list[dict]:
    return [
        {
            "symbol": row.symbol,
            "direction": row.direction,
            "entry": row.entry,
            "stop_loss": row.stop_loss,
            "target": row.target,
            "quantity": row.ai_quantity,
            "confidence": row.confidence,
            "risk_reward": row.risk_reward,
            "capital_required": row.capital_required,
            "risk_amount": row.risk_amount,
            "risk_percent": row.risk_percent,
        }
        for row in snapshot.rows
    ]


def _telegram_message(
    *,
    now: datetime,
    account_equity: float,
    available_funds: float | None,
    funds_source: str,
    snapshot: StockMonitorSnapshot,
    new_signals: list[PaperSignal],
    rejection_breakdown: Counter,
    source_breakdown: Counter,
) -> str:
    funds_text = "N/A"
    if available_funds is not None:
        funds_text = f"₹{available_funds:,.2f}"
    lines = [
        "🤖 Stockmarket Bot — monitor heartbeat",
        f"Time: {now.astimezone(IST).strftime('%d-%b-%Y %I:%M:%S %p IST')}",
        "Mode: PAPER-TRADING (no real orders)",
        f"Available Dhan funds: {funds_text} [{funds_source}]",
        f"Scan equity used: ₹{account_equity:,.2f}",
        f"Stocks scanned: {snapshot.scanned_count}",
        f"Actionable: {snapshot.actionable_count} | BUY: {snapshot.buy_count} | SELL: {snapshot.sell_count}",
        f"New paper signals frozen: {len(new_signals)}",
    ]

    if snapshot.rows:
        lines.append("")
        lines.append("🎯 Suggested stocks")
        for row in snapshot.rows[:5]:
            lines.append(
                f"{row.direction} {row.symbol} | Entry ₹{row.entry:,.2f} | "
                f"SL ₹{row.stop_loss:,.2f} | Target ₹{row.target:,.2f} | "
                f"Qty {row.ai_quantity} | Conf {row.confidence:.0%} | R:R {row.risk_reward:.2f}"
            )
    else:
        lines.append("No actionable stock passed all configured gates in this cycle.")

    if rejection_breakdown:
        top = ", ".join(f"{name}={count}" for name, count in rejection_breakdown.most_common(5))
        lines.append(f"Filters: {top}")
    if source_breakdown:
        sources = ", ".join(f"{name}={count}" for name, count in source_breakdown.items())
        lines.append(f"Data sources: {sources}")
    lines.append("Dashboard: refresh the Streamlit app for the latest heartbeat.")
    return "\n".join(lines)[:3900]


def main() -> int:
    now = datetime.now(timezone.utc)
    store = ValidationStore(os.getenv("VALIDATION_JOURNAL_PATH", DEFAULT_JOURNAL_PATH))
    notifier = TelegramNotifier()
    account_equity, available_funds, funds_source, funds_error = _resolve_account_equity(notifier)

    try:
        scan, snapshot = run_stock_monitor(account_equity=account_equity)
    except Exception as exc:
        error = str(exc)
        failure_status = {
            "status": "FAILED",
            "updated_at": now.isoformat(),
            "error": error,
            "account_equity": round(account_equity, 2),
            "available_funds": available_funds,
            "funds_source": funds_source,
        }
        _write_status(failure_status)
        print(f"Monitor cycle failed: {error}", file=sys.stderr)
        try:
            notifier.send(f"⚠️ Monitor cycle FAILED\nTime: {now.astimezone(IST).strftime('%d-%b-%Y %I:%M:%S %p IST')}\nError: {error}")
        except Exception as notify_exc:
            print(f"(Telegram alert also failed: {notify_exc})", file=sys.stderr)
        return 1

    already_open = _symbols_with_open_signal_today(store, today=now.astimezone(IST).date())
    new_signals = signals_from_snapshot(snapshot, generated_at=now, already_open=already_open)
    for signal in new_signals:
        store.append_signal(signal)

    rejection_breakdown = Counter(
        result.data_status for result in scan.results if result.data_status != "ACTIONABLE"
    )
    source_breakdown = Counter(
        result.data_source for result in scan.results if result.data_source
    )
    actionable_rows = _candidate_payload(snapshot)

    status_payload = {
        "status": "OK",
        "updated_at": now.isoformat(),
        "mode": "paper-trading",
        "market_window": "09:15-15:30 IST weekdays",
        "account_equity": round(account_equity, 2),
        "available_funds": available_funds,
        "funds_source": funds_source,
        "funds_error": funds_error,
        "telegram_configured": notifier.configured,
        "scan": {
            "requested": scan.requested_count,
            "scanned": scan.scanned_count,
            "data_errors": scan.data_error_count,
            "quality_failures": scan.quality_failure_count,
            "technical_rejections": scan.technical_rejection_count,
            "fundamental_errors": scan.fundamental_error_count,
            "candidate_count": scan.candidate_count,
            "actionable": scan.actionable_count,
            "buy": scan.buy_count,
            "sell": scan.sell_count,
        },
        "rejection_breakdown": dict(rejection_breakdown),
        "data_sources": dict(source_breakdown),
        "new_signals_frozen": len(new_signals),
        "actionable_candidates": actionable_rows,
    }
    _write_status(status_payload)

    print(
        f"Cycle complete: {snapshot.scanned_count} scanned, "
        f"{snapshot.actionable_count} actionable, {len(new_signals)} new signal(s) frozen."
    )
    print(f"Rejection breakdown: {dict(rejection_breakdown)}")
    print(f"Data sources: {dict(source_breakdown)}")

    message = _telegram_message(
        now=now,
        account_equity=account_equity,
        available_funds=available_funds,
        funds_source=funds_source,
        snapshot=snapshot,
        new_signals=new_signals,
        rejection_breakdown=rejection_breakdown,
        source_breakdown=source_breakdown,
    )
    try:
        delivered = notifier.send(message)
        if not delivered:
            print("Telegram is not configured; heartbeat was printed locally.")
        else:
            print("Telegram heartbeat delivered.")
    except Exception as exc:
        print(f"Telegram heartbeat failed: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
