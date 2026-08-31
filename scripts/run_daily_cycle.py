"""Run one bounded complete trading cycle.

The cycle observes the complete NSE cash-equity universe in one bulk quote stage,
then spends expensive candle/AI/research work only on the highest-information
shortlist. This is the architecture that keeps a 5-minute scheduled fallback
from being killed by a fixed timeout.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))

from intraday_bot.runtime_v2 import run_cycle

if __name__=="__main__":
    result=run_cycle()
    print("=== COMPLETE PAPER-TRADING CYCLE ===")
    print(f"Universe observed : {result.get('stocks_observed',0)}")
    print(f"Quotes received   : {result.get('quotes',0)}")
    print(f"Candidates        : {len(result.get('candidates',[]))}")
    print(f"Rejections        : {result.get('rejections',{})}")
    print(f"Orders/signals    : {len(result.get('orders',[]))}")
    print(f"Open positions    : {result.get('positions_open',0)}")
    print(f"Realized P&L      : ₹{result.get('realized_pnl',0):,.2f}")
    print(f"Duration          : {result.get('duration_seconds',0):.2f}s")
    print(f"Errors            : {len(result.get('errors',[]))}")
    if result.get('errors'):
        for e in result['errors'][:20]: print('ERROR:',e)
    raise SystemExit(0 if not result.get('errors') else 1)
