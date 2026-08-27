"""Entry point for one continuous-monitor cycle, invoked by GitHub Actions.

New in this repo -- not part of the original 33 files. This is the glue
between monitor_service.run_stock_monitor() and validation_store.ValidationStore
that didn't exist in what was shared. It's intentionally narrow: it only
freezes new paper signals. EOD closing and daily/weekly report generation are
a separate script (run_eod_close.py, not yet built) run once per day rather
than every 15-minute cycle -- mixing the two here would risk closing a signal
against bars from the same cycle that opened it.

This script never places a broker order. It calls only the read-only
monitor pipeline and the broker-independent paper-trading journal.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime, timezone

from src.monitor_service import run_stock_monitor
from src.paper_trading_validation import PaperSignal
from src.stock_monitor import StockMonitorSnapshot
from src.telegram_notify import TelegramNotifier
from src.validation_store import ValidationStore

DEFAULT_JOURNAL_PATH = "data/paper_trading_journal.jsonl"


def _account_equity() -> float:
    raw = os.getenv("BOT_RESEARCH_REFERENCE_CAPITAL", "100000").strip()
    try:
        return float(raw)
    except ValueError:
        return 100000.0


def _symbols_with_open_signal_today(store: ValidationStore, *, today: date) -> set[str]:
    """Symbols that already have a signal frozen today with no outcome yet.

    Without this check, a candidate that's still actionable on the next
    15-minute scan would get frozen again -- inflating signal counts and
    double-counting the same trade idea across cycles. One symbol gets at
    most one frozen signal per day; it's held until EOD close, not re-frozen
    every cycle it remains actionable.
    """
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
    """Freeze one paper signal per newly-actionable symbol.

    Uses each row's AI-suggested quantity, not any dashboard override -- a
    paper signal frozen here represents what the bot itself would have done,
    which is what Phase 9 needs to judge.
    """
    signals = []
    for row in snapshot.rows:
        if row.symbol in already_open:
            continue
        signals.append(PaperSignal(
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
        ))
    return signals


def main() -> int:
    now = datetime.now(timezone.utc)
    store = ValidationStore(os.getenv("VALIDATION_JOURNAL_PATH", DEFAULT_JOURNAL_PATH))
    notifier = TelegramNotifier()

    try:
        _, snapshot = run_stock_monitor(account_equity=_account_equity())
    except Exception as exc:
        # A failed cycle is a real problem worth a red X in Actions history,
        # not something to silently swallow -- but it should never crash in
        # a way that leaves no record of why.
        print(f"Monitor cycle failed: {exc}", file=sys.stderr)
        try:
            notifier.send(f"\u26a0\ufe0f Monitor cycle failed: {exc}")
        except Exception as notify_exc:
            print(f"(Telegram alert also failed: {notify_exc})", file=sys.stderr)
        return 1

    already_open = _symbols_with_open_signal_today(store, today=now.date())
    new_signals = signals_from_snapshot(snapshot, generated_at=now, already_open=already_open)
    for signal in new_signals:
        store.append_signal(signal)

    print(
        f"Cycle complete: {snapshot.scanned_count} scanned, "
        f"{snapshot.actionable_count} actionable, "
        f"{len(new_signals)} new signal(s) frozen "
        f"({len(snapshot.rows) - len(new_signals)} already open today)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
