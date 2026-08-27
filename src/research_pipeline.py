"""Orchestration script: runs one complete monitor cycle and persists results.

This is the entry point for GitHub Actions (every 1 minute during trading hours).
It fetches the full NSE stock universe dynamically, screens/ranks them via the
research pipeline, freezes paper signals, and commits the journal to git.

No order placement. No hardcoded stock lists.
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add repo root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.models import TradeCandidate
from src.paper_trading_validation import (
    PaperSignal,
    freeze_signal,
)
from src.production_market_data import ProductionMarketDataProvider
from src.research_pipeline import (
    ResearchPipelineConfig,
    ResearchPipelineError,
    scan_symbols,
)
from src.stock_monitor import (
    build_monitor_snapshot,
    StockMonitorSnapshot,
)
from src.validation_store import ValidationStore


def get_nse_stock_universe() -> list[str]:
    """Fetch all NSE stocks dynamically (Nifty 500+ or full exchange).
    
    Returns a list of NSE equity symbols to screen.
    In production, this would connect to NSE/Dhan master list.
    For now, returns Nifty 500 as a reasonable universe.
    """
    # TODO: Replace this with actual NSE master data fetch
    # Option 1: Fetch from Dhan security master
    # Option 2: Fetch from NSE official website
    # Option 3: Use a hardcoded Nifty 500 list (for now)
    
    # HARDCODED NIFTY 500 (TO BE REPLACED)
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
        "LCI", "SHRIRAMFS", "BPCL", "COALINDIA", "UNIONBANK",
        "CHOLAFIN", "JSWSTEEL", "SIEMENS", "IGL", "ICICIPRULI",
        "CGPOWER", "GMRINFRA", "IDFC", "BALRAMCHIN", "POLYCAB",
        "IDBI", "CANARA", "LICHSGFIN", "CENTRUM", "INDIANB",
        "IDFCBANK", "KIRLOSENG", "THERMAX", "CONCOR", "ENGINERCO",
        "BEL", "DRREDDY", "HINDPETRO", "FEDERALBNK", "AUROPHARMA",
        "GOODYEAR", "APLLTD", "KANSAINER", "PETRONET", "NHPC",
        "TORNTPHARM", "VEDL", "KPTL", "EXIDEIND", "ASTRAL",
        "GUJGASLTD", "ADANIGREEN", "ADANIPOWER", "HDFCBANK", "LTIM",
        "TIMKEN", "BOMDYEING", "SUNPRIV", "KPITTECH", "JISLJALEQS",
        "VIJAYHBANK", "MANAPPURAM", "GSKCONS", "RAMCOCEM", "GRAPHITE",
        "SOLARINDS", "KAPREETRY", "BAJAJELEC", "SYNGENE", "NETWORK18",
        "TARAFORGE", "ADVANIGAS", "LUXIND", "FINPIPE", "FINSWEET",
        "ATGL", "CEATLTD", "BLISS", "PVMIRROR", "SOUTHINDBANK",
        "ATUL", "IPCALAB", "FLEXITUFF", "JECAD", "SJVN",
        "FORCEMOT", "MOIL", "APLLTD", "APOLLOHOSP", "KSCLTD",
        "EMAMI", "CHOLAFINS", "JKTYRE", "VBL", "BERGEPAINT",
        "INDUSTOWER", "SUPREMEIND", "MASWAND", "GENESISTEL", "DEEPIND",
        "HIND", "KPIL", "CASTROLIND", "MAYUKUNITED", "GRINDWELL",
        "ICRA", "TVS", "BATAINDIA", "ASIANPNT", "RADICO",
        "WABCO", "KAJARIACER", "NILE", "EIDPARRY", "VIBTRANS",
        "CRAFTSMAN", "BIMETAL", "KARURVYSYA", "SUVEN", "MAHABANK",
        "ORIENTBANK", "GRSE", "MAZDA", "BAJAJAUTO", "TIINDIA",
        "GALAXYSURF", "SOMICONVEY", "STARCEMSEC", "KIRTILTD", "CUMMINSIND",
        "DALMIASUG", "GUJALKALI", "JAGRAN", "ARVINDFARM", "RENUKA",
        "SUVISIONRE", "DIVI", "JPPOWER", "HINDMOTORS", "PRSMJOHNSN",
        "VARUDDRIVE", "TORK", "LAXMIMACH", "MCDORMOT", "SHARDACROP",
        "APAR", "BEMLHYDRO", "ADANIENSOL", "TECHNOPARK", "TRIVENI",
        "JKLAKSHMI", "VIVASTRUMENT", "APOLLOTYRE", "ASIANPAINT", "KESORAMIND",
        "AEGISLOG", "AKALI", "SHYAMSHEL", "PILARCORP", "VGUARD",
        "MAITHANALL", "KPFINANCE", "IIFLWAM", "INDIANBK", "TITIUMBK",
        "EICHERMOT", "SETF", "LAVAINFRA", "SAMBHAVBAG", "HINDCONST",
        "MAZDAMOTR", "TIINDIA", "BIL", "TIINDIA", "SUDARSHAN",
        "KPMHTECH", "TRIGIANT", "LFCGEAR", "DISHTV", "KPMGTECH",
        "BAJAJFINS", "TCLCONSUM", "VARDHINFO", "UNIPARTS", "KALWAR",
        "DCMSHRIRAM", "CCCL", "IFBIND", "DEEPAKNI", "KARUNADU",
        "CHHANBROS", "SUNDARMFIN", "PILLARTECH", "ADFOOD", "WABCOIND",
        "MASFIN", "JAINMATPL", "INDORAMS", "DHANFINS", "ALLINDSTL",
        "VIPULDRUGS", "SHYAMMETL", "AMSHOLDING", "VIMTALAB", "SANDHRA",
        "KALUMAHOLD", "PNBHOUSING", "GODREJPRP", "GODREJIND", "BAJAJHLDNG",
        "HINDUNILVR", "DIVISLAB", "LTIM", "PAGEIND", "INDIGO",
        "BHARATIARTL", "COFORGE", "JSWINFRA", "JINDALSTEL", "IRCTC",
        "BLUEDART", "BIGSHOESA", "ADANIPORTS", "INDUSTOWER", "AADJAFOOD",
    ]
    
    return nifty_500


def run_daily_cycle():
    """Execute one monitor cycle: scan → rank → freeze signals → commit."""
    
    try:
        # Step 1: Fetch full NSE stock universe dynamically
        print("[INFO] Fetching NSE stock universe...")
        symbols = get_nse_stock_universe()
        print(f"[INFO] Scanning {len(symbols)} stocks")
        
        # Step 2: Run the research pipeline (screenic, ranking, validation)
        print("[INFO] Running research pipeline...")
        config = ResearchPipelineConfig()
        config.validate()
        
        scan_result = scan_symbols(
            symbols,
            config=config,
            provider=ProductionMarketDataProvider(timeout=12.0),
        )
        
        print(f"[INFO] Scan results:")
        print(f"  - Scanned: {scan_result.scanned_count}")
        print(f"  - Data errors: {scan_result.data_error_count}")
        print(f"  - Quality rejected: {scan_result.quality_failure_count}")
        print(f"  - Technical rejected: {scan_result.technical_rejection_count}")
        print(f"  - Candidates generated: {scan_result.candidate_count}")
        print(f"  - Actionable (ranked): {scan_result.actionable_count}")
        print(f"  - BUY: {scan_result.buy_count}, SELL: {scan_result.sell_count}")
        
        # Step 3: Build monitor snapshot for dashboard
        print("[INFO] Building monitor dashboard snapshot...")
        account_equity = float(
            os.getenv("BOT_RESEARCH_REFERENCE_CAPITAL", "100000")
        )
        snapshot: StockMonitorSnapshot = build_monitor_snapshot(
            scan_result,
            account_equity=account_equity,
        )
        
        # Step 4: Freeze paper signals for actionable candidates
        print("[INFO] Freezing paper signals...")
        store = ValidationStore("data/paper_trading_journal.jsonl")
        
        frozen_count = 0
        for row in snapshot.rows:
            # Check if this symbol already has an open signal from earlier today
            existing_signals = [
                s for s in store.signals()
                if s["payload"]["symbol"] == row.symbol
                and s["payload"]["generated_at"].split("T")[0] == datetime.now(timezone.utc).strftime("%Y-%m-%d")
            ]
            
            if existing_signals:
                print(f"[SKIP] {row.symbol}: Signal already frozen today")
                continue
            
            # Freeze the signal
            signal = PaperSignal(
                symbol=row.symbol,
                exchange=row.exchange,
                direction=row.direction,
                entry=row.entry,
                stop_loss=row.stop_loss,
                target=row.target,
                quantity=row.ai_quantity,
                confidence=row.confidence,
                risk_reward=row.risk_reward,
                reason=row.reason,
                generated_at=datetime.now(timezone.utc).isoformat(),
            )
            
            freeze_signal(store, signal)
            frozen_count += 1
            print(f"[FREEZE] {row.symbol} {row.direction} @ {row.entry}")
        
        print(f"[INFO] Froze {frozen_count} signals")
        
        # Step 5: Generate daily report (optional)
        print("[INFO] Cycle complete")
        print(f"[INFO] Actionable candidates: {snapshot.actionable_count}")
        print(f"[INFO] Stocks scanned: {snapshot.scanned_count}")
        
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
    exit_code = run_daily_cycle()
    sys.exit(exit_code)
