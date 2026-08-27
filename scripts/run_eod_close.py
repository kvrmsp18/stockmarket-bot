"""Close open paper signals at EOD and persist daily/weekly validation reports.

This script is paper-trading only. It never places broker orders. Target/stop
resolution is performed from future intraday bars first; otherwise an open
signal is closed at the latest available EOD bar.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.paper_trading_validation import (
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
IST = ZoneInfo("Asia/Kolkata")


def _load_signals(store: ValidationStore) -> tuple[list[PaperSignal], set[str]]:
    signals: list[PaperSignal] = []
    for record in store.signals():
        payload = record["payload"]
        signals.append(PaperSignal(**payload))
    resolved_ids = {record["payload"]["signal_id"] for record in store.outcomes()}
    return signals, resolved_ids


def _parse_signal_date(signal: PaperSignal) -> date:
    value = signal.generated_at
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(IST).date()


def _append_summary_once(store: ValidationStore, summary, report_type: str) -> bool:
    for record in store.summaries(report_type):
        payload = record["payload"]
        if (
            payload.get("period_start") == summary.period_start.isoformat()
            and payload.get("period_end") == summary.period_end.isoformat()
        ):
            return False
    store.append_summary(summary, report_type=report_type)
    return True


def _write_report(summary, report_type: str) -> Path:
    if report_type == "daily":
        name = f"{summary.period_start.isoformat()}.md"
        path = Path("reports/daily") / name
        title = "Paper-Trading Daily Report"
    else:
        iso = summary.period_start.isocalendar()
        name = f"{iso.year}-W{iso.week:02d}.md"
        path = Path("reports/weekly") / name
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
                print(f"{signal.symbol}: outcome remains {outcome.outcome}; not recorded as a terminal result.")
        except Exception as exc:
            errors.append(f"{signal.symbol}: {exc}")

    outcomes = [
        record["payload"]
        for record in store.outcomes()
    ]

    # Build the daily summary from signals generated on today's IST date.
    today_outcomes = [
        _outcome_from_payload(payload)
        for payload in outcomes
        if _payload_date(payload) == today
    ]
    daily = summarize_outcomes(today_outcomes, period_start=today, period_end=today)
    daily_added = _append_summary_once(store, daily, "daily")
    _write_report(daily, "daily")

    # The weekly summary is appended once, after Friday's EOD close. This avoids
    # double-counting the same weekly period in the readiness gate.
    weekly_added = False
    if today.weekday() == 4:
        week_start, week_end = week_bounds(today)
        weekly_outcomes = [
            _outcome_from_payload(payload)
            for payload in outcomes
            if week_start <= _payload_date(payload) <= week_end
        ]
        weekly = summarize_outcomes(weekly_outcomes, period_start=week_start, period_end=week_end)
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
        f"Reports: daily={'updated' if daily_added else 'already recorded'}",
    ]
    if today.weekday() == 4:
        message_lines.append(f"Weekly report: {'updated' if weekly_added else 'already recorded'}")
    if no_data:
        message_lines.append(f"⚠️ Signals without terminal data: {no_data}")
    if errors:
        message_lines.append(f"⚠️ Data errors: {len(errors)}")
        message_lines.extend(errors[:3])

    print("\n".join(message_lines))
    try:
        if notifier.send("\n".join(message_lines)[:3900]):
            print("Telegram EOD report delivered.")
        else:
            print("Telegram is not configured; EOD report remains in GitHub reports.")
    except Exception as exc:
        print(f"Telegram EOD report failed: {exc}")

    return 0


def _payload_date(payload: dict) -> date:
    value = datetime.fromisoformat(str(payload["generated_at"]).replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(IST).date()


def _outcome_from_payload(payload: dict):
    from src.paper_trading_validation import PaperOutcome
    return PaperOutcome(**payload)


if __name__ == "__main__":
    raise SystemExit(main())
