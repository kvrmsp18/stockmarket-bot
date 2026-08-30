"""Run one complete paper-trading monitor cycle.

The GitHub Actions workflow supplies a fresh NSE universe through
BOT_MARKET_UNIVERSE. Falls back to top 25 liquid stocks if not set.
No live orders are placed.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.paper_trading_validation import PaperSignal
from src.research_pipeline import ResearchPipelineConfig, ResearchPipelineError, scan_symbols
from src.stock_monitor import build_monitor_snapshot
from src.validation_store import ValidationStore


# TOP 25 LIQUID NSE STOCKS — fallback only, expanded as scan speed improves
FALLBACK_UNIVERSE = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
    "SBIN", "WIPRO", "BAJAJFINSV", "LT", "ITC",
    "MARUTI", "AXISBANK", "KOTAKBANK", "BHARTI", "M&M",
    "NTPC", "TATASTEEL", "ADANIPORTS", "SUNPHARMA", "TITAN",
    "HINDUNILVR", "ONGC", "POWERGRID", "COALINDIA", "EICHERMOT",
]


def get_nse_stock_universe() -> list[str]:
    """Read NSE universe from workflow env var; fallback to top 25 if needed."""
    raw = os.getenv("BOT_MARKET_UNIVERSE", "").strip()

    if raw:
        symbols: list[str] = []
        seen: set[str] = set()
        for item in raw.split(","):
            sym = item.strip().upper()
            if sym and sym not in seen:
                symbols.append(sym)
                seen.add(sym)
        if len(symbols) >= 10:
            print(f"[INFO] Universe from workflow: {len(symbols)} symbols")
            return symbols
        print(f"[WARN] BOT_MARKET_UNIVERSE too small ({len(symbols)}); using fallback")

    print(f"[WARN] BOT_MARKET_UNIVERSE not set; using top-25 fallback")
    return FALLBACK_UNIVERSE


def _reference_capital() -> float:
    raw = os.getenv("BOT_RESEARCH_REFERENCE_CAPITAL", "").strip()
    try:
        value = float(raw) if raw else 100000.0
    except (TypeError, ValueError):
        value = 100000.0
    return value if value > 0 else 100000.0


def run_daily_cycle() -> int:
    try:
        print("[START] Daily monitor cycle")

        # ── 1. Universe ──────────────────────────────────────────────────────
        symbols = get_nse_stock_universe()
        print(f"[INFO] Scanning {len(symbols)} symbols...")

        # ── 2. Research pipeline ─────────────────────────────────────────────
        config = ResearchPipelineConfig()
        config.validate()

        scan_result = scan_symbols(symbols, config=config, provider=None)

        print("[INFO] Scan results:")
        print(f"  Requested : {scan_result.requested_count}")
        print(f"  Scanned   : {scan_result.scanned_count}")
        print(f"  Data err  : {scan_result.data_error_count}")
        print(f"  Qual rej  : {scan_result.quality_failure_count}")
        print(f"  Tech rej  : {scan_result.technical_rejection_count}")
        print(f"  Fund err  : {scan_result.fundamental_error_count}")
        print(f"  Candidates: {scan_result.candidate_count}")
        print(f"  Actionable: {scan_result.actionable_count}  (BUY:{scan_result.buy_count} SELL:{scan_result.sell_count})")

        # ── 3. Dashboard snapshot ────────────────────────────────────────────
        equity = _reference_capital()
        snapshot = build_monitor_snapshot(scan_result, account_equity=equity)

        # ── 4. Freeze signals ────────────────────────────────────────────────
        store = ValidationStore("data/paper_trading_journal.jsonl")
        generated_at = datetime.now(timezone.utc)
        today = generated_at.date().isoformat()

        existing_today = {
            rec.get("payload", {}).get("symbol")
            for rec in store.signals()
            if str(rec.get("payload", {}).get("generated_at", "")).startswith(today)
        }

        frozen_count = 0
        for row in snapshot.rows:
            if row.symbol in existing_today:
                print(f"[SKIP]   {row.symbol}: already frozen today")
                continue

            signal = PaperSignal(
                signal_id=f"{row.symbol}-{generated_at.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}",
                symbol=row.symbol,
                direction=row.direction,
                generated_at=generated_at,
                entry=float(row.entry),
                stop_loss=float(row.stop_loss),
                target=float(row.target),
                quantity=int(row.ai_quantity),
                confidence=float(row.confidence),
                risk_reward=float(row.risk_reward),
            )
            store.append_signal(signal)
            frozen_count += 1
            print(f"[FREEZE] {row.symbol} {row.direction} @ {row.entry}")

        print(f"[INFO] Froze {frozen_count} new paper signals")
        print("[SUCCESS] Cycle complete")
        return 0

    except ResearchPipelineError as exc:
        print(f"[ERROR] Pipeline: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[ERROR] Unexpected: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(run_daily_cycle())
