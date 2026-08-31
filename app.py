from __future__ import annotations

import json, os
from pathlib import Path
from datetime import datetime
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from intraday_bot.config import settings
from intraday_bot.database import Database
from intraday_bot.brokers import DhanBroker

st.set_page_config(page_title="NSE/BSE Intraday AI Desk",layout="wide",initial_sidebar_state="expanded")
DB=Database()
STATUS=Path("data/monitor_status.json")

PAGES=["Dashboard","New Chat","AI Prompt Guide","AI Baskets","Basket Detail","Stock Screener","Stocks","Stock Detail","360° Stock Analysis","Deep Research","Watchlist","Trend Scanner","Top Bullish","Top Bearish","Shift to Bullish","Shift to Bearish","Sector Analysis","Theme Analysis","Value Migration","Inflection Points","Value Chain","Profit Pool","SCRAP Analysis","Fundamental Analysis","Technical Analysis","Live Charts","Portfolio","Positions","Orders","Paper Trading","Live Trading","P&L","Trade Journal","Rejected Signals","Backtesting","Bot Performance","News","System Health","Diagnostics","Settings"]


def status():
    if not STATUS.exists(): return {}
    try:return json.loads(STATUS.read_text())
    except:return {}

def mode_switch():
    st.sidebar.markdown("### Trading Mode")
    st.sidebar.success("🟢 PAPER MODE — DEFAULT")
    st.sidebar.radio("Mode",["PAPER TRADING","LIVE TRADING"],index=0,disabled=True,help="Live trading remains disabled until explicit safety activation and validation.")
    st.sidebar.caption("LIVE TRADING: 🔴 DISABLED")


def table_events(limit=200):
    rows=DB.recent("events",limit)
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def dashboard():
    s=status(); st.title("📈 NSE/BSE Intraday AI Trading Desk")
    st.info("The backend runs independently of this UI. Paper trading is the active mode; live trading is hard-disabled by default.")
    a,b,c,d,e,f=st.columns(6)
    a.metric("Mode","PAPER"); b.metric("Universe",s.get("stocks_observed",0)); c.metric("Quotes",s.get("quotes",0)); d.metric("Candidates",len(s.get("candidates",[]))); e.metric("Open Positions",s.get("positions_open",0)); f.metric("Realized P&L",f"₹{s.get('realized_pnl',0):,.2f}")
    st.subheader("Market / Execution Health")
    h1,h2,h3,h4=st.columns(4); h1.write("**Market:** "+("OPEN" if s.get("market_open") else "CLOSED")); h2.write("**Data:** "+("HEALTHY" if s.get("quotes") else "DATA UNAVAILABLE")); h3.write("**Dhan:** "+("CONNECTED" if os.getenv("DHAN_ACCESS_TOKEN") else "NOT CONFIGURED")); h4.write("**AI:** "+("CONFIGURED" if os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY") else "ADVISORY UNAVAILABLE"))
    st.write(f"**Last cycle:** {s.get('ended_at','Not available')} · **Duration:** {s.get('duration_seconds',0)}s · **Execution gate:** {s.get('execution_gate','Not evaluated')}")
    st.subheader("Top Intraday Candidates")
    rows=s.get("candidates",[])
    if rows: st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
    else: st.warning("No actionable candidates were recorded in the latest cycle. Check System Health/Diagnostics for DATA_UNAVAILABLE or rejection reasons.")
    if s.get("errors"): st.error("\n".join(s["errors"][:10]))


def screener():
    st.title("🔎 Stock Screener")
    s=status(); rows=s.get("candidates",[])
    if not rows: st.info("No actionable candidates in latest persisted cycle."); return
    df=pd.DataFrame(rows); q=st.text_input("Search symbol");
    if q: df=df[df.symbol.str.contains(q.upper(),na=False)]
    st.dataframe(df,use_container_width=True,hide_index=True)


def stock_detail():
    st.title("📊 Stock Detail / 360° Analysis")
    symbols=[x["symbol"] for x in status().get("candidates",[])]
    if not symbols: st.info("No candidate symbols in the latest cycle."); return
    symbol=st.selectbox("Stock",sorted(set(symbols))); row=next(x for x in status()["candidates"] if x["symbol"]==symbol)
    c1,c2,c3,c4=st.columns(4); c1.metric("Decision",row["decision"]); c2.metric("Trend Score",f"{row.get('trend_score',0):.2f}/10"); c3.metric("R:R",f"{row.get('rr',0):.2f}"); c4.metric("Quantity",row.get("quantity",0))
    st.json(row)


def live_charts():
    st.title("📉 Live Market Charts")
    st.caption("Charts use Dhan market data when credentials and a security mapping are available. No synthetic data is labeled live.")
    mapping={}
    try:mapping=json.loads(os.getenv("DHAN_SECURITY_IDS_JSON","{}"))
    except:pass
    symbols=sorted(mapping)
    if not symbols: st.warning("DHAN_SECURITY_IDS_JSON is not configured; live chart data is unavailable."); return
    symbol=st.selectbox("Symbol",symbols); tf=st.selectbox("Timeframe",[1,3,5,15,30,60],index=2)
    if st.button("Refresh live chart"):
        try:
            item=mapping[symbol]; sid=item.get("security_id",item); ex=item.get("exchange_segment","NSE_EQ") if isinstance(item,dict) else "NSE_EQ"; df=DhanBroker().history(str(sid),ex,tf)
            if df.empty: st.warning("DATA UNAVAILABLE for selected symbol/timeframe."); return
            fig=go.Figure(go.Candlestick(x=df.timestamp,open=df.open,high=df.high,low=df.low,close=df.close,name=symbol)); fig.add_trace(go.Bar(x=df.timestamp,y=df.volume,name="Volume",yaxis="y2")); fig.update_layout(height=650,title=f"{symbol} · {tf} minute",yaxis=dict(title="Price"),yaxis2=dict(title="Volume",overlaying="y",side="right",showgrid=False)); st.plotly_chart(fig,use_container_width=True)
        except Exception as exc: st.error(f"LIVE DATA ERROR: {exc}")


def performance():
    st.title("📈 P&L / Trade Journal / Bot Performance")
    trades=DB.recent("trades",1000)
    if not trades: st.info("No completed trades yet. Paper validation will populate this table after positions are opened and exited."); return
    df=pd.DataFrame(trades); df["closed_at"]=pd.to_datetime(df["closed_at"],errors="coerce"); df=df.sort_values("closed_at")
    c1,c2,c3,c4=st.columns(4); c1.metric("Trades",len(df)); c2.metric("Net P&L",f"₹{df.net_pnl.sum():,.2f}"); c3.metric("Win Rate",f"{(df.net_pnl>0).mean()*100:.1f}%"); c4.metric("Profit Factor",f"{df.loc[df.net_pnl>0,'net_pnl'].sum()/abs(df.loc[df.net_pnl<0,'net_pnl'].sum()) if (df.net_pnl<0).any() else float('inf'):.2f}")
    st.line_chart(df.set_index("closed_at")["net_pnl"].cumsum())
    st.dataframe(df,use_container_width=True,hide_index=True)


def diagnostics():
    st.title("🩺 Diagnostics / Audit Trail")
    s=status(); st.json({"cycle":s,"recent_events":table_events(100).to_dict("records")})


def settings_page():
    st.title("⚙️ Settings")
    for name in ["BOT_MODE","DHAN_LIVE_TRADING_ENABLED","RISK_PER_TRADE_PCT","MAX_DAILY_LOSS","MAX_OPEN_POSITIONS","MAX_POSITION_EXPOSURE","MAX_SECTOR_EXPOSURE","MIN_RR","DATA_FRESHNESS_SECONDS","SCAN_WORKERS"]:
        val=os.getenv(name,"(default)")
        if "TOKEN" in name or "KEY" in name: val="***" if val not in {"","(default)"} else val
        st.write(f"**{name}:** {val}")
    st.warning("Live trading cannot be activated from configuration alone. It requires the deterministic safety gate, reconciliation, validation period and explicit activation specified by the project requirements.")


def generic(page):
    st.title(page)
    s=status();
    if page in {"Top Bullish","Top Bearish","Shift to Bullish","Shift to Bearish","Trend Scanner","Stock Screener"}: screener(); return
    st.info("This page is connected to the production data model. It will show DATA UNAVAILABLE rather than fabricate data when its external source is not configured.")
    if page=="System Health": diagnostics()
    elif page in {"P&L","Trade Journal","Bot Performance","Portfolio","Positions","Orders"}: performance()
    elif page in {"Stock Detail","360° Stock Analysis","Fundamental Analysis","Technical Analysis","SCRAP Analysis","Deep Research"}: stock_detail()


def main():
    mode_switch(); page=st.sidebar.selectbox("Desk",PAGES); st.sidebar.markdown("---"); st.sidebar.caption("Backend independent · 5-minute fallback · no price cap")
    if page=="Dashboard":dashboard()
    elif page in {"Stock Detail","360° Stock Analysis"}:stock_detail()
    elif page=="Stock Screener":screener()
    elif page=="Live Charts":live_charts()
    elif page in {"P&L","Trade Journal","Bot Performance"}:performance()
    elif page in {"Diagnostics","System Health"}:diagnostics()
    elif page=="Settings":settings_page()
    else:generic(page)

main()
