from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from intraday_bot.database import Database


def main():
    db=Database(); trades=db.recent("trades",5000); events=db.recent("events",5000)
    pnl=sum(float(x.get("net_pnl",0) or 0) for x in trades); wins=sum(1 for x in trades if float(x.get("net_pnl",0) or 0)>0); losses=sum(1 for x in trades if float(x.get("net_pnl",0) or 0)<0)
    gross_profit=sum(float(x.get("net_pnl",0) or 0) for x in trades if float(x.get("net_pnl",0) or 0)>0); gross_loss=sum(float(x.get("net_pnl",0) or 0) for x in trades if float(x.get("net_pnl",0) or 0)<0)
    report={"date":datetime.now().date().isoformat(),"mode":"PAPER","number_of_trades":len(trades),"winning_trades":wins,"losing_trades":losses,"breakeven_trades":len(trades)-wins-losses,"net_pnl":pnl,"gross_profit":gross_profit,"gross_loss":gross_loss,"win_rate":wins/len(trades) if trades else 0,"profit_factor":gross_profit/abs(gross_loss) if gross_loss else None,"positions_open":int(db.scalar("SELECT COUNT(*) FROM positions WHERE closed_at IS NULL") or 0),"signals":int(db.scalar("SELECT COUNT(*) FROM signals") or 0),"events":len(events)}
    Path("reports/daily").mkdir(parents=True,exist_ok=True); Path(f"reports/daily/{report['date']}.json").write_text(json.dumps(report,indent=2),encoding="utf-8"); print(json.dumps(report,indent=2))

if __name__=="__main__":main()
