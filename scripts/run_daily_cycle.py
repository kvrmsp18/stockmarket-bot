"""Single bounded 5-minute monitoring cycle for the new platform.

Also keeps the legacy validation-store helpers required by the existing test
suite.  These helpers are pure paper-trading utilities and do not place live
orders.
"""
from __future__ import annotations

import sys
import uuid
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from intraday_bot.runtime import run_cycle
from src.paper_trading_validation import PaperSignal


def signals_from_snapshot(snapshot, generated_at: datetime, already_open: set[str] | None = None) -> list[PaperSignal]:
    """Convert actionable monitor rows into immutable paper signals.

    The AI-calculated quantity is deliberately used, never a dashboard/manual
    quantity override.  ``already_open`` is a same-cycle/same-day guard.
    """
    blocked = {str(x).upper() for x in (already_open or set())}
    signals: list[PaperSignal] = []

    for row in snapshot.rows:
        symbol = str(row.symbol).upper()
        if symbol in blocked:
            continue
        if getattr(row, "status", "ACTIONABLE") != "ACTIONABLE":
            continue
        if row.direction not in {"BUY", "SELL"}:
            continue

        signals.append(
            PaperSignal(
                signal_id=f"{symbol}-{generated_at.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}",
                symbol=symbol,
                direction=row.direction,
                generated_at=generated_at,
                entry=float(row.entry),
                stop_loss=float(row.stop_loss),
                target=float(row.target),
                quantity=int(row.ai_quantity),
                confidence=float(row.confidence),
                risk_reward=float(row.risk_reward),
            )
        )

    return signals


def _symbols_with_open_signal_today(store, today: date | None = None) -> set[str]:
    """Return symbols having a currently unresolved signal generated today."""
    target_day = today or datetime.now().date()
    resolved_ids = {
        str(record.get("payload", {}).get("signal_id"))
        for record in store.outcomes()
        if record.get("payload", {}).get("signal_id")
    }

    result: set[str] = set()
    for record in store.signals():
        payload = record.get("payload", {})
        signal_id = str(payload.get("signal_id", ""))
        if signal_id in resolved_ids:
            continue
        generated = payload.get("generated_at")
        if not generated:
            continue
        try:
            generated_day = datetime.fromisoformat(str(generated).replace("Z", "+00:00")).date()
        except (TypeError, ValueError):
            continue
        if generated_day == target_day:
            symbol = str(payload.get("symbol", "")).upper()
            if symbol:
                result.add(symbol)
    return result


if __name__ == "__main__":
    result = run_cycle()
    print("=== COMPLETE PAPER-TRADING CYCLE ===")
    for key in ("stocks_observed", "quotes", "duration_seconds", "positions_open", "realized_pnl"):
        print(f"{key}: {result.get(key)}")
    print(f"candidates: {len(result.get('candidates', []))}")
    print(f"rejections: {result.get('rejections', {})}")
    print(f"orders: {len(result.get('orders', []))}")
    print(f"errors: {result.get('errors', [])}")
    raise SystemExit(0 if not result.get("errors") else 1)
