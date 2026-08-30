"""Run one complete paper-trading monitor cycle.

The GitHub Actions workflow supplies a fresh complete NSE cash-equity universe
through BOT_MARKET_UNIVERSE on every cycle. If not available, falls back to
Nifty 500. Market-data scanning is intentionally delegated to scan_symbols
without an injected provider so its bounded worker pool is used.
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


# NIFTY 500 FALLBACK (used only if BOT_MARKET_UNIVERSE not supplied by workflow)
NIFTY_500_FALLBACK = [
    "RELIANCE", "TCS", "INFY", "HINDUNILVR", "ICICIBANK",
    "SBIN", "WIPRO", "BAJAJFINSV", "LT", "ITC",
    "MARUTI", "AXISBANK", "KOTAKBANK", "ONGC", "BAJAJPALM",
    "BHARATI", "M&M", "NTPC", "TATASTEEL", "ADANIPORTS",
    "JIOTASKS", "PIDILITIND", "TECHM", "GAIL", "EICHERMOT",
    "SUNPHARMA", "HEROMOTOCO", "HDFCLIFE", "TATAMOTORS", "SBICARD",
    "BHARATFORGE", "INDIGO", "POWERGRID", "BHEL", "DLF",
    "BANKINDIA", "MOTHERSON", "UPL", "HAVELLS", "ESCORTS",
    "SBILIFE", "INDIAMART", "LALPATHLAB", "PHARMEASY", "MOBILESUM",
    "DMART", "PAGEIND", "MINDTREE", "TITAN", "MPHASIS",
]


def get_nse_stock_universe() -> list[str]:
    """Read NSE universe from workflow env var, fallback to Nifty 500 if needed.
    
    Priority:
    1. BOT_MARKET_UNIVERSE from workflow (fresh full exchange)
    2. NIFTY_500_FALLBACK (emergency backup)
    
    Always ensures at least 50 symbols to prevent accidental empty scans.
    """
    raw = os.getenv("BOT_MARKET_UNIVERSE", "").strip()
    
    if raw:
        # Parse workflow-supplied universe
        symbols: list[str] = []
        seen: set[str] = set()
        for item in raw.split(","):
            symbol = item.strip().upper()
            if symbol and symbol not in seen:
                symbols.append(symbol)
                seen.add(symbol)
        
        if len(symbols) >= 50:
            print(f"[INFO] Using BOT_MARKET_UNIVERSE: {len(symbols)} symbols from workflow")
            return symbols
        else:
            print(f"[WARN] BOT_MARKET_UNIVERSE has only {len(symbols)} symbols; using Nifty 500 fallback")
    else:
        print("[WARN] BOT_MARKET_UNIVERSE not set; using Nifty 500 fallback")
    
    # Fallback to Nifty 500
    return NIFTY_500_FALLBACK


def _reference_capital() -> float:
    """Return a safe positive research capital value even when the GitHub var is blank."""
    raw = os.getenv("BOT_RESEARCH_REFERENCE_CAPITAL", "").strip()
    try:
        value = float(raw) if raw else 100000.0
    except (TypeError, ValueError):
        value = 100000.0
    return value if value > 0 else 100000.0


def run_daily_cycle() -> int:
    try:
        print("[INFO] Loading NSE cash-equity universe...")
        symbols = get_nse_stock_universe()
        print(f"[INFO] Universe size: {len(symbols)} symbols")
        print("[INFO] Running complete-market research scan with bounded parallel workers...")

        config = ResearchPipelineConfig()
        config.validate()

        # Do NOT inject ProductionMarketDataProvider here. scan_symbols uses
        # one independent provider per worker when provider=None, which is the
        # intended bounded-parallel production path.
        scan_result = scan_symbols(symbols, config=config, provider=None)

        print("[INFO] Scan results:")
        print(f"  - Requested: {scan_result.requested_count}")
        print(f"  - Scanned: {scan_result.scanned_count}")
        print(f"  - Data errors: {scan_result.data_error_count}")
        print(f"  - Quality rejected: {scan_result.quality_failure_count}")
        print(f"  - Technical rejected: {scan_result.technical_rejection_count}")
        print(f"  - Fundamental errors: {scan_result.fundamental_error_count}")
        print(f"  - Candidates generated: {scan_result.candidate_count}")
        print(f"  - Actionable: {scan_result.actionable_count}")
        print(f"  - BUY: {scan_result.buy_count}, SELL: {scan_result.sell_count}")

        print("[INFO] Building monitor dashboard snapshot...")
        snapshot = build_monitor_snapshot(
            scan_result,
            account_equity=_reference_capital(),
        )

        print("[INFO] Freezing paper signals...")
        store = ValidationStore("data/paper_trading_journal.jsonl")
        frozen_count = 0
        generated_at = datetime.now(timezone.utc)
        today = generated_at.date().isoformat()
        existing_today = {
            record.get("payload", {}).get("symbol")
            for record in store.signals()
            if str(record.get("payload", {}).get("generated_at", "")).startswith(today)
        }

        for row in snapshot.rows:
            if row.symbol in existing_today:
                print(f"[SKIP] {row.symbol}: signal already frozen today")
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
        print("[INFO] Cycle complete")
        return 0

    except ResearchPipelineError as exc:
        print(f"[ERROR] Research pipeline failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[ERROR] Unexpected error: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(run_daily_cycle())
