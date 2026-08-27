"""Close open paper signals at EOD and persist daily/weekly validation reports.

This script is paper-trading only. It never places broker orders. Target/stop
resolution is performed from future intraday bars first; otherwise an open
signal is closed at the latest available intraday bar.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.paper_trading_validation import (
    PaperOutcome,
    PaperSignal,
    close_at_eod,
    render_validation_report,
    summarize_outcomes,
    week_bounds,
)
from src.production_market_data import ProductionMarketDataProvider
from src.telegram_notify import TelegramNotifier
from src.validation_store import ValidationStore

DEFAULT_JOURNAL_PATH = "data/paper_trading_journal.jsonl"
DEFAULT_CRITERIA_PATH = "config/validation_criteria.json"
IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


def _parse_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _load_signals(store: ValidationStore) -> tuple[list[PaperSignal], set[str]]:
    signals: list[PaperSignal] = []
    for record in store.signals():
        payload = dict(record["payload"])
        payload["generated_at"] = _parse_datetime(payload["generated_at"])
        signals.append(PaperSignal(**payload))
    resolved_ids = {record["payload"]["signal_id"] for record in store.outcomes()}
    return signals, resolved_ids


def _payload_date(payload: dict) -> date:
    return _parse_datetime(payload["generated_at"]).astimezone(IST).date()


def _outcome_from_payload(payload: dict) -> PaperOutcome:
    value = dict(payload)
    value["generated_at"] = _parse_datetime(value["generated_at"])
    if value.get("exit_at"):
        value["exit_at"] = _parse_datetime(value["exit_at"])
    return PaperOutcome(**value)


def _validation_start() -> date | None:
    path = Path(os.getenv("VALIDATION_CRITERIA_PATH", DEFAULT_CRITERIA_PATH))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return date.fromisoformat(str(payload["validation_period_start"]))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _append_summary_once(store: ValidationStore, summary, report_type: str) -> bool:
    for record in store.summaries(report_type):
        payload = record["payload"]
        if payload.get("period_start") == summary.period_start.isoformat() and payload.get("period_end") == summary.period_end.isoformat():
            return False
    store.append_summary(summary, report_type=report_type)
    return True


def _write_report(summary, report_type: str) -> Path:
    if report_type == "daily":
        path = Path("reports/daily") / f"{summary.period_start.isoformat()}.md"
        title = "Paper-Trading Daily Report"
    else:
        iso = summary.period_start.isocalendar()
        path = Path("reports/weekly") / f"{iso.year}-W{iso.week:02d}.md"
        title = "Paper-Trading Weekly Report"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_validation_report(summary, title=title), encoding="utf-8")
    return path


def main() -> int:
    today = datetime.now(IST).date()
    store = ValidationStore(os.getenv("VALIDATION_JOURNAL_PATH", DEFAULT_JOURNAL_PATH))
    notifier = TelegramNotifier()
    provider = ProductionMarketDataProvider(timeout=12.0)

    signals, resolved_ids = _load_signals(store)
    open_signals = [signal for signal in signals if signal.signal_id not in resolved_ids]
    closed = 0
    no_data = 0
    errors: list[str] = []

    for signal in open_signals:
        try:
            bars = provider.history(signal.symbol, period="5d", interval="15m")
            outcome = close_at_eod(signal, bars)
            if outcome.outcome in {"TARGET", "STOP", "EOD_CLOSE"}:
                store.append_outcome(outcome)
                closed += 1
            else:
                no_data += 1
                print(f"{signal.symbol}: outcome remains {outcome.outcome}; not recorded as terminal.")
        except Exception as exc:
            errors.append(f"{signal.symbol}: {exc}")

    outcomes = [_outcome_from_payload(record["payload"]) for record in store.outcomes()]
    today_outcomes = [item for item in outcomes if _payload_date(item.__dict__) == today]
    daily = summarize_outcomes(today_outcomes, period_start=today, period_end=today)
    daily_added = _append_summary_once(store, daily, "daily")
    _write_report(daily, "daily")

    weekly_added = False
    if today.weekday() == 4:
        week_start, week_end = week_bounds(today)
        validation_start = _validation_start()
        summary_start = max(week_start, validation_start) if validation_start else week_start
        weekly_outcomes = [
            item for item in outcomes
            if summary_start <= _payload_date(item.__dict__) <= week_end
        ]
        weekly = summarize_outcomes(weekly_outcomes, period_start=summary_start, period_end=week_end)
        weekly_added = _append_summary_once(store, weekly, "weekly")
        _write_report(weekly, "weekly")

    message_lines = [
        "📊 Stockmarket Bot — EOD paper-trading report",
        f"Date: {today.strftime('%d-%b-%Y')} IST",
        f"Signals closed this EOD run: {closed}",
        f"Daily signals: {daily.signals} | Target: {daily.target_count} | Stop: {daily.stop_count} | EOD close: {daily.eod_close_count}",
        f"Daily net P&L after estimated charges: ₹{daily.net_pnl:,.2f}",
        f"Daily profit factor: {'N/A' if daily.profit_factor is None else f'{daily.profit_factor:.2f}'}",
        f"Daily max drawdown: ₹{daily.max_drawdown:,.2f}",
        f"Daily report: {'updated' if daily_added else 'already recorded'}",
    ]
    if today.weekday() == 4:
        message_lines.append(f"Weekly report: {'updated' if weekly_added else 'already recorded'}")
    if no_data:
        message_lines.append(f"⚠️ Signals without terminal data: {no_data}")
    if errors:
        message_lines.append(f"⚠️ Data errors: {len(errors)}")
        message_lines.extend(errors[:3])

    report_message = "\n".join(message_lines)
    print(report_message)
    try:
        if notifier.send(report_message[:3900]):
            print("Telegram EOD report delivered.")
        else:
            print("Telegram is not configured; EOD report remains in GitHub reports.")
    except Exception as exc:
        print(f"Telegram EOD report failed: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
