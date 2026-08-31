"""Single bounded 5-minute monitoring cycle for the new platform."""
from __future__ import annotations
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from intraday_bot.runtime import run_cycle

if __name__=="__main__":
    result=run_cycle()
    print("=== COMPLETE PAPER-TRADING CYCLE ===")
    for k in ("stocks_observed","quotes","duration_seconds","positions_open","realized_pnl"):
        print(f"{k}: {result.get(k)}")
    print(f"candidates: {len(result.get('candidates',[]))}")
    print(f"rejections: {result.get('rejections',{})}")
    print(f"orders: {len(result.get('orders',[]))}")
    print(f"errors: {result.get('errors',[])}")
    raise SystemExit(0 if not result.get("errors") else 1)
