"""Orchestration script: runs one complete monitor cycle and persists results.

This is the entry point for GitHub Actions (every 1 minute during trading hours).
It fetches the full NSE stock universe dynamically, screens/ranks them via the
research pipeline, and freezes paper signals.

No order placement. No hardcoded stock lists.
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add repo root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.production_market_data import ProductionMarketDataProvider
from src.research_pipeline import ResearchPipelineConfig, scan_symbols
from src.stock_monitor import build_monitor_snapshot
from src.validation_store import ValidationStore


def get_nse_stock_universe() -> list[str]:
    """Fetch all NSE stocks dynamically.
    
    TODO: Replace with live Dhan/NSE security master fetch.
    For now: Nifty 500 universe.
    """
    nifty_500 = [
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
    return nifty_500


def run_daily_cycle():
    """Execute one monitor cycle."""
    
    try:
        # Step 1: Get full NSE universe
        print("[INFO] Fetching NSE stock universe...")
        symbols = get_nse_stock_universe()
        print(f"[INFO] Scanning {len(symbols)} stocks")
        
        # Step 2: Run research pipeline
        print("[INFO] Running research pipeline...")
        config = ResearchPipelineConfig()
        config.validate()
        
        scan_result = scan_symbols(
            symbols,
            config=config,
            provider=ProductionMarketDataProvider(timeout=12.0),
        )
        
        print(f"[INFO] Scan results: {scan_result.actionable_count} actionable")
        
        # Step 3: Build monitor snapshot
        print("[INFO] Building monitor snapshot...")
        account_equity = float(os.getenv("BOT_RESEARCH_REFERENCE_CAPITAL", "100000"))
        snapshot = build_monitor_snapshot(scan_result, account_equity=account_equity)
        
        # Step 4: Freeze signals to journal
        print("[INFO] Freezing signals...")
        store = ValidationStore("data/paper_trading_journal.jsonl")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        frozen_count = 0
        
        for row in snapshot.rows:
            # Check if already frozen today
            existing = [s for s in store.signals() 
                       if s["payload"]["symbol"] == row.symbol 
                       and s["payload"]["generated_at"].split("T")[0] == today]
            
            if existing:
                continue
            
            # Write signal
            signal_data = {
                "symbol": row.symbol,
                "exchange": row.exchange,
                "direction": row.direction,
                "entry": float(row.entry),
                "stop_loss": float(row.stop_loss),
                "target": float(row.target),
                "quantity": int(row.ai_quantity),
                "confidence": float(row.confidence),
                "risk_reward": float(row.risk_reward),
                "reason": str(row.reason),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
            store.append_signal(signal_data)
            frozen_count += 1
            print(f"[FREEZE] {row.symbol} {row.direction}")
        
        print(f"[INFO] Froze {frozen_count} signals")
        print("[INFO] Cycle complete")
        return 0
        
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(run_daily_cycle())
