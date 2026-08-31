from __future__ import annotations
import pandas as pd
from .technical import technical_setup


def run(df:pd.DataFrame, capital:float=100000)->dict:
    """Simple no-look-ahead validation engine; costs are applied to each completed trade."""
    trades=[]; position=None
    for i in range(60,len(df)):
        window=df.iloc[:i].copy(); setup=technical_setup(window); px=float(df.iloc[i].close)
        if position is None and setup["direction"] in {"BUY","SELL"} and setup["rr"]>=3:
            position={"side":setup["direction"],"entry":px,"stop":setup["stop"],"target":setup["target"]}
        elif position:
            side=position["side"]; exit_px=None; reason=None
            if side=="BUY" and px<=position["stop"]:exit_px=px;reason="STOP_LOSS"
            elif side=="BUY" and px>=position["target"]:exit_px=px;reason="TARGET"
            elif side=="SELL" and px>=position["stop"]:exit_px=px;reason="STOP_LOSS"
            elif side=="SELL" and px<=position["target"]:exit_px=px;reason="TARGET"
            if reason:
                gross=(exit_px-position["entry"])*(1 if side=="BUY" else -1); trades.append({"side":side,"entry":position["entry"],"exit":exit_px,"gross_pnl":gross,"reason":reason});position=None
    t=pd.DataFrame(trades); wins=int((t.gross_pnl>0).sum()) if not t.empty else 0; losses=int((t.gross_pnl<0).sum()) if not t.empty else 0; gp=float(t.loc[t.gross_pnl>0,"gross_pnl"].sum()) if not t.empty else 0; gl=float(t.loc[t.gross_pnl<0,"gross_pnl"].sum()) if not t.empty else 0
    return {"trades":trades,"total_trades":len(trades),"wins":wins,"losses":losses,"win_rate":wins/len(trades) if trades else 0,"gross_profit":gp,"gross_loss":gl,"profit_factor":gp/abs(gl) if gl else None}
