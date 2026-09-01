from __future__ import annotations

import json
import os
import tempfile
import time
from urllib.request import urlopen
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from intraday_bot.brokers import DhanBroker
from intraday_bot.config import settings
from intraday_bot.database import Database
from intraday_bot.research import FRAMEWORK_RULES
from intraday_bot.runtime import LIVE_TEST_MODE, PAPER_MODE, run_cycle

st.set_page_config(page_title="NSE/BSE Intraday AI Trading Desk", layout="wide", initial_sidebar_state="expanded")
DB = Database(); STATUS = Path("data/monitor_status.json"); HEARTBEAT = Path("data/worker_heartbeat.json")
SCHEDULER_HEARTBEAT = Path("data/scheduler_heartbeat.json"); WATCHDOG_HEARTBEAT = Path("data/watchdog_heartbeat.json")
PAGES = ["Dashboard","New Chat","AI Prompt Guide","AI Baskets","Basket Detail","Stock Screener","Stocks","Stock Detail","360° Stock Analysis","Deep Research","Watchlist","Trend Scanner","Top Bullish","Top Bearish","Shift to Bullish","Shift to Bearish","Sector Analysis","Theme Analysis","Value Migration","Inflection Points","Value Chain","Profit Pool","SCRAP Analysis","Fundamental Analysis","Technical Analysis","Live Charts","Portfolio","Positions","Orders","Paper Trading","Live Trading","P&L","Trade Journal","Rejected Signals","Backtesting","Bot Performance","News","System Health","Diagnostics","Settings"]


def secret(name: str, default: str = "") -> str:
    value = os.getenv(name, "")
    if value: return value
    try: return str(st.secrets.get(name, default) or default)
    except Exception: return default


def sync_remote_state(force: bool = False) -> bool:
    """Keep Streamlit Cloud synchronized with the GitHub Actions state store."""
    now=time.time()
    last=float(st.session_state.get("_remote_state_sync",0.0))
    if not force and now-last < 60: return True
    base="https://raw.githubusercontent.com/kvrmsp18/stockmarket-bot/main/data/"
    targets={"trading.db":Path("data/trading.db"),"monitor_status.json":Path("data/monitor_status.json"),"scheduler_heartbeat.json":Path("data/scheduler_heartbeat.json"),"worker_heartbeat.json":Path("data/worker_heartbeat.json"),"watchdog_heartbeat.json":Path("data/watchdog_heartbeat.json")}
    ok=False
    for name,dest in targets.items():
        try:
            dest.parent.mkdir(parents=True,exist_ok=True)
            with urlopen(base+name,timeout=8) as r: data=r.read()
            fd,tmp=tempfile.mkstemp(prefix="remote-state-",dir=str(dest.parent))
            with os.fdopen(fd,"wb") as f: f.write(data)
            os.replace(tmp,dest); ok=True
        except Exception:
            try:
                if 'tmp' in locals() and os.path.exists(tmp): os.unlink(tmp)
            except Exception: pass
    st.session_state["_remote_state_sync"]=now
    return ok


def status() -> dict[str, Any]:
    try: return json.loads(STATUS.read_text(encoding="utf-8")) if STATUS.exists() else {}
    except Exception: return {}


def heartbeat() -> dict[str, Any]:
    """Return the freshest persisted heartbeat available on Streamlit Cloud."""
    candidates = []
    for path in (HEARTBEAT, SCHEDULER_HEARTBEAT, WATCHDOG_HEARTBEAT):
        if not path.exists(): continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload: candidates.append(payload)
        except Exception: continue
    if not candidates: return {}
    return max(candidates, key=lambda x: str(x.get("updated_at", "")))


def sql(sql: str, params: tuple[Any, ...] = ()) -> pd.DataFrame:
    try:
        with DB.connect() as con:
            rows = con.execute(sql, params).fetchall()
        return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()
    except Exception as exc:
        st.error(f"DATABASE ERROR: {exc}"); return pd.DataFrame()


def today_mask(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns: return pd.Series(False, index=df.index)
    return pd.to_datetime(df[col], errors="coerce", utc=True).dt.tz_convert("Asia/Kolkata").dt.date == date.today()


def selected_mode() -> str:
    return str(st.session_state.get("trading_mode", PAPER_MODE)).upper()


def mode_switch() -> None:
    st.sidebar.markdown("### Trading Mode")
    choice = st.sidebar.radio("Mode", ["PAPER TRADING", "LIVE TRADING"], index=0 if selected_mode() == PAPER_MODE else 1, key="mode_radio")
    st.session_state["trading_mode"] = PAPER_MODE if choice == "PAPER TRADING" else LIVE_TEST_MODE
    if selected_mode() == PAPER_MODE:
        st.sidebar.success("🟢 PAPER MODE — SIMULATED ORDERS")
        st.sidebar.caption("Uses real Dhan market data. Orders/fills are simulated.")
    else:
        st.sidebar.warning("🟠 LIVE TRADING — TEST MODE ONLY")
        st.sidebar.caption("No live broker order is sent. This mode exercises the live-style workflow with simulated fills.")


def controls() -> None:
    mode = selected_mode()
    st.sidebar.markdown("### Bot Controls")
    if st.sidebar.button("🔄 Refresh Search / Dashboard", use_container_width=True):
        sync_remote_state(force=True)
        st.cache_data.clear()
        st.rerun()
    label = "▶ Run Paper Analysis" if mode == PAPER_MODE else "▶ Run Live Test Analysis"
    if st.sidebar.button(label, type="primary", use_container_width=True):
        description = "paper-analysis" if mode == PAPER_MODE else "live-test analysis"
        with st.spinner(f"Running complete {description} cycle..."):
            result = run_cycle(mode)
        if result.get("errors"): st.sidebar.error(f"Cycle completed with {len(result['errors'])} error(s)")
        else: st.sidebar.success(f"Cycle complete · {len(result.get('candidates', []))} candidate(s)")
        st.rerun()
    hb = heartbeat()
    if hb: st.sidebar.caption(f"Worker: {hb.get('state','UNKNOWN')} · {hb.get('updated_at','—')}")
    else: st.sidebar.caption("Worker heartbeat: NOT FOUND")


def header(title: str, desc: str = "") -> None:
    st.title(title)
    if desc: st.info(desc)


def dashboard() -> None:
    s = status(); hb = heartbeat()
    header("📈 NSE/BSE Intraday AI Trading Desk", "Paper mode uses real Dhan market data and simulated fills. Live Trading selection is TEST ONLY and also uses simulated fills; the Dhan order endpoint is never called by this test mode.")
    if hb.get("state") == "ERROR": st.error(f"24/7 WORKER ERROR: {hb.get('message','Unknown error')}")
    elif hb: st.success(f"24/7 WORKER: {hb.get('state','RUNNING')} · last heartbeat {hb.get('updated_at','—')}")
    else: st.warning("24/7 WORKER: heartbeat unavailable. Start scripts/worker.py on the always-on server.")
    a,b,c,d,e,f = st.columns(6)
    a.metric("Mode", s.get("mode", selected_mode())); b.metric("Universe", s.get("stocks_observed",0)); c.metric("Quotes", s.get("quotes",0)); d.metric("Candidates",len(s.get("candidates",[]))); e.metric("Open Positions",s.get("positions_open",0)); f.metric("Today's P&L",f"₹{s.get('today_realized_pnl',0):,.2f}")
    st.subheader("TODAY'S RESEARCH / ACTIONABLE TRADES")
    candidates = pd.DataFrame(s.get("candidates", []))
    if candidates.empty: st.warning("No actionable candidates were recorded in the latest cycle. Check Diagnostics and Rejected Signals for the rejection funnel.")
    else:
        cols=[c for c in ["symbol","decision","price","entry_low","entry_high","max_chase","stop","target","rr","quantity","capital_required","max_risk","potential_reward","trend_score","technical_score","fundamental_score","valuation_score","conviction_score","framework_agreement","ai_consensus","reason"] if c in candidates.columns]
        st.dataframe(candidates[cols],use_container_width=True,hide_index=True)
    st.subheader("SIMULATED TRADE RESULT")
    trades=sql("SELECT * FROM trades WHERE mode IN ('PAPER','LIVE_TEST') ORDER BY closed_at DESC")
    if trades.empty: st.info("No completed simulated trades yet. Positions remain open until target, stop or EOD square-off.")
    else:
        t=trades[today_mask(trades,"closed_at")]; st.metric("Completed today",len(t)); st.metric("Today's realized net P&L",f"₹{pd.to_numeric(t.get('net_pnl',pd.Series(dtype=float)),errors='coerce').fillna(0).sum():,.2f}")
    st.subheader("BOT HEALTH")
    h1,h2,h3,h4=st.columns(4); h1.write("**Market:** "+("OPEN" if s.get("market_open") else "CLOSED")); h2.write("**Dhan:** "+("CONNECTED" if secret("DHAN_ACCESS_TOKEN") else "NOT CONFIGURED")); h3.write("**AI:** "+("CONFIGURED" if secret("OPENAI_API_KEY") or secret("ANTHROPIC_API_KEY") else "ADVISORY UNAVAILABLE")); h4.write("**Execution:** "+s.get("execution_gate","NOT EVALUATED"))
    st.caption(f"Last cycle: {s.get('ended_at','—')} · duration {s.get('duration_seconds',0)}s · errors {len(s.get('errors',[]))}")


def framework_table(payload: dict[str, Any]) -> None:
    rows=[]
    for name, x in payload.get("frameworks",{}).items():
        rows.append({"Framework":name,"Score":x.get("score",0),"Confidence":x.get("confidence",0),"Positive factors":", ".join(x.get("positive_factors",[])) or "—","Negative factors":", ".join(x.get("negative_factors",[])) or "—","Missing data":", ".join(x.get("missing_data",[])) or "—","Method":x.get("method","")})
    if rows: st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)


def framework_page() -> None:
    header("🧠 Buffett / Jhunjhunwala / Lynch / 100 Baggers / CANSLIM", "Five named frameworks are shown separately with evidence, missing data and confidence. They are research/conviction inputs, never standalone intraday BUY triggers.")
    st.warning("Framework scores are only as complete as the persisted fundamental dataset. Missing values are explicitly shown and are NOT treated as negative evidence.")
    latest=sql("SELECT payload,symbol,ts FROM events WHERE event_type='FRAMEWORK_ANALYSIS' ORDER BY id DESC LIMIT 200")
    if latest.empty: st.info("No persisted framework analyses yet. Run Analysis Now during market hours, or start the always-on worker."); return
    symbols=sorted(latest["symbol"].dropna().astype(str).unique()); symbol=st.selectbox("Stock",symbols,key="framework_stock")
    row=latest[latest["symbol"].astype(str).eq(symbol)].iloc[0]
    try: payload=json.loads(row["payload"])
    except Exception: payload={}
    st.write(f"**Last research timestamp:** {row['ts']}")
    st.write(f"**Overall framework conviction:** {payload.get('frameworks',{}).get('overall','DATA UNAVAILABLE')} · **Agreement:** {payload.get('frameworks',{}).get('agreement','DATA UNAVAILABLE')}")
    framework_table(payload.get("frameworks",{}))
    st.subheader("Framework methodology")
    for name,spec in FRAMEWORK_RULES.items(): st.write(f"**{name}:** {spec['label']} · factors: {', '.join(spec['factors'])}")


def research_page(title: str) -> None:
    s = status(); candidates = pd.DataFrame(s.get("candidates", []))
    research = sql("SELECT ts,symbol,event_type,payload FROM events WHERE component='research' ORDER BY id DESC LIMIT 500")
    header(title, f"{title} — persisted Bot results only.")
    if title == "Trend Scanner":
        if candidates.empty: st.warning("No actionable trend candidates in the latest cycle. Check Rejected Signals and Diagnostics.")
        else:
            cols=[c for c in ["symbol","decision","price","trend_score","technical_score","volume_score","rr","stop","target","reason"] if c in candidates.columns]
            st.dataframe(candidates.sort_values("trend_score",ascending=False)[cols],use_container_width=True,hide_index=True)
        return
    if title == "Stock Screener":
        if research.empty: st.warning("DATA UNAVAILABLE: no persisted research records yet."); return
        st.caption("Research-stage records for the latest scanned symbols."); st.dataframe(research,use_container_width=True,hide_index=True); return
    if title in {"Stocks","Stock Detail","360° Stock Analysis"}:
        symbols=sorted(set(candidates.get("symbol",pd.Series(dtype=str)).dropna().astype(str))) or sorted(set(research.get("symbol",pd.Series(dtype=str)).dropna().astype(str)))
        if not symbols: st.warning("DATA UNAVAILABLE: no stock-level research records yet."); return
        symbol=st.selectbox("Stock",symbols,key=f"stock_selector_{title}")
        if not candidates.empty and symbol in set(candidates.get("symbol",[])): st.json(candidates[candidates["symbol"]==symbol].iloc[0].to_dict())
        else: st.dataframe(research[research["symbol"].astype(str)==symbol],use_container_width=True,hide_index=True)
        if title == "360° Stock Analysis": framework_page()
        return
    if title in {"AI Baskets","Basket Detail","Deep Research"}: framework_page(); return
    if title in {"Top Bullish","Shift to Bullish"}:
        if candidates.empty: st.warning("No bullish actionable candidates recorded."); return
        df=candidates[pd.to_numeric(candidates.get("trend_score",0),errors="coerce")>=7].sort_values("trend_score",ascending=False); st.dataframe(df,use_container_width=True,hide_index=True); return
    if title in {"Top Bearish","Shift to Bearish"}:
        if candidates.empty: st.warning("No bearish actionable candidates recorded."); return
        df=candidates[pd.to_numeric(candidates.get("trend_score",10),errors="coerce")<4].sort_values("trend_score",ascending=True); st.dataframe(df,use_container_width=True,hide_index=True); return
    if title == "Rejected Signals": rejected_page(); return
    if title in {"Sector Analysis","Theme Analysis","Value Migration","Inflection Points","Value Chain","Profit Pool"}:
        if candidates.empty: st.warning("No current actionable research data for this view."); return
        group_col="sector" if title=="Sector Analysis" else "theme"
        if group_col in candidates.columns:
            summary=candidates.groupby(group_col,dropna=False).agg(candidates=("symbol","count"),avg_trend=("trend_score","mean")).reset_index()
            if "conviction_score" in candidates.columns: summary["avg_conviction"]=candidates.groupby(group_col,dropna=False)["conviction_score"].mean().values
            st.dataframe(summary,use_container_width=True,hide_index=True)
        else: st.info("The latest cycle does not contain this dimension yet.")
        return
    if title in {"SCRAP Analysis","Fundamental Analysis","Technical Analysis"}:
        if research.empty: st.warning("DATA UNAVAILABLE: no persisted research records yet."); return
        st.dataframe(research,use_container_width=True,hide_index=True); return
    if title == "Watchlist":
        st.info("Watchlist is driven by persisted actionable candidates; no manual stock list is substituted.")
        if not candidates.empty: st.dataframe(candidates,use_container_width=True,hide_index=True)
        return
    st.info("This research view has no dedicated persisted dataset yet. No generic cycle data is substituted.")


def paper_page() -> None:
    header("📝 Paper Trading","Real Dhan market data + deterministic simulated execution. No live order is placed.")
    s=status(); c=pd.DataFrame(s.get("candidates",[])); orders=sql("SELECT * FROM orders WHERE order_id LIKE 'PAPER-%' ORDER BY ts DESC")
    a,b,c1,d=st.columns(4); a.metric("Actionable",len(c)); b.metric("Paper orders",len(orders[today_mask(orders,'ts')]) if not orders.empty else 0); c1.metric("Open positions",s.get('positions_open',0)); d.metric("Today's P&L",f"₹{s.get('today_realized_pnl',0):,.2f}")
    if not c.empty: st.dataframe(c,use_container_width=True,hide_index=True)
    st.subheader("Paper Order Ledger"); st.dataframe(orders,use_container_width=True,hide_index=True) if not orders.empty else st.info("No paper orders yet.")


def live_page() -> None:
    header("🟠 Live Trading — Test Mode","This is deliberately a broker-safe test mode. It uses the same analysis/risk/entry/position workflow but creates simulated fills. It does NOT call Dhan /v2/orders and cannot place a real order.")
    st.warning("LIVE TRADING IS NOT ACTUAL LIVE EXECUTION YET. Because no live-order API/funded account is configured, this page is for end-to-end testing only.")
    s=status(); live_orders=sql("SELECT * FROM orders WHERE order_id LIKE 'LIVETEST-%' ORDER BY ts DESC")
    test_pnl=sql("SELECT COALESCE(SUM(net_pnl),0) AS x FROM trades WHERE mode='LIVE_TEST'"); pnl_value=float(test_pnl.get("x",pd.Series([0])).iloc[0]) if not test_pnl.empty else 0.0
    open_tests=sql("SELECT * FROM positions WHERE mode='LIVE_TEST' AND closed_at IS NULL")
    a,b,c,d=st.columns(4); a.metric("Test Candidates",len(s.get("candidates",[])) if s.get("mode")==LIVE_TEST_MODE else 0); b.metric("Simulated Live-Test Orders",len(live_orders)); c.metric("Open Test Positions",len(open_tests)); d.metric("Test P&L",f"₹{pnl_value:,.2f}")
    if st.button("▶ Run Live Test Now",type="primary"):
        with st.spinner("Running live-style analysis with simulated execution..."): result=run_cycle(LIVE_TEST_MODE)
        if result.get("errors"): st.error(f"Test cycle completed with {len(result['errors'])} error(s)")
        else: st.success(f"Test cycle complete · {len(result.get('candidates',[]))} candidate(s)")
        st.rerun()
    st.subheader("Live-Test Order Ledger"); st.dataframe(live_orders,use_container_width=True,hide_index=True) if not live_orders.empty else st.info("No simulated live-test orders yet.")


def portfolio_page() -> None:
    header("📊 Portfolio","Paper and live-test simulated positions and completed trades from the persisted ledger.")
    pos=sql("SELECT * FROM positions ORDER BY opened_at DESC"); trades=sql("SELECT * FROM trades WHERE mode IN ('PAPER','LIVE_TEST') ORDER BY closed_at DESC")
    openp=pos[pos["closed_at"].isna()] if not pos.empty and "closed_at" in pos else pd.DataFrame(); realized=pd.to_numeric(trades.get("net_pnl",pd.Series(dtype=float)),errors="coerce").fillna(0).sum()
    a,b,c,d=st.columns(4); a.metric("Open Positions",len(openp)); b.metric("Open Exposure",f"₹{(pd.to_numeric(openp.get('entry_price',pd.Series(dtype=float)),errors='coerce').fillna(0)*pd.to_numeric(openp.get('quantity',pd.Series(dtype=float)),errors='coerce').fillna(0)).sum():,.2f}"); c.metric("Realized P&L",f"₹{realized:,.2f}"); d.metric("Reference Capital",f"₹{settings.reference_capital:,.2f}")
    st.dataframe(openp,use_container_width=True,hide_index=True) if not openp.empty else st.info("No open simulated positions.")


def pnl_page() -> None:
    header("💰 P&L","Completed simulated-trade P&L only; unrealized positions are not counted as realized P&L.")
    df=sql("SELECT * FROM trades WHERE mode IN ('PAPER','LIVE_TEST') ORDER BY closed_at ASC")
    if df.empty: st.info("No completed simulated trades yet."); return
    df["net_pnl"]=pd.to_numeric(df["net_pnl"],errors="coerce").fillna(0); t=df[today_mask(df,"closed_at")]
    a,b,c,d=st.columns(4); a.metric("Today's P&L",f"₹{t.net_pnl.sum():,.2f}"); b.metric("Total P&L",f"₹{df.net_pnl.sum():,.2f}"); c.metric("Win Rate",f"{(df.net_pnl>0).mean()*100:.1f}%"); losses=abs(df.loc[df.net_pnl<0,'net_pnl'].sum()); wins=df.loc[df.net_pnl>0,'net_pnl'].sum(); d.metric("Profit Factor",f"{wins/losses:.2f}" if losses else "∞"); st.dataframe(df,use_container_width=True,hide_index=True)


def health_page() -> None:
    header("🩺 System Health","Operational state of the always-on scheduler, market-data pipeline and latest cycle.")
    s=status(); hb=heartbeat(); a,b,c,d,e=st.columns(5); a.metric("Worker",hb.get("state","NOT FOUND")); b.metric("Market","OPEN" if s.get("market_open") else "CLOSED"); c.metric("Quotes",s.get("quotes",0)); d.metric("Actionable",len(s.get("candidates",[]))); e.metric("Errors",len(s.get("errors",[]))); st.json({"worker":hb,"latest_cycle":s})


def diagnostics_page() -> None:
    header("🔍 Diagnostics","Full persisted troubleshooting events; no fake success state is generated.")
    s=status(); st.json(s); df=sql("SELECT * FROM events WHERE severity IN ('ERROR','WARN') OR event_type NOT IN ('CYCLE_START','CYCLE_END') ORDER BY id DESC LIMIT 500")
    st.dataframe(df,use_container_width=True,hide_index=True) if not df.empty else st.info("No diagnostic events persisted.")


def rejected_page() -> None:
    header("⛔ Rejected Signals","Every rejected candidate is persisted with its rejection reason and evidence.")
    df=sql("SELECT ts,symbol,payload FROM events WHERE event_type='SIGNAL_REJECTED' ORDER BY id DESC LIMIT 500")
    if df.empty: st.info("No rejected signals persisted yet."); return
    rows=[]
    for _,r in df.iterrows():
        try:p=json.loads(r.payload)
        except Exception:p={}
        rows.append({"timestamp":r.ts,"symbol":r.symbol,"reason":p.get("rejection_reason"),"message":p.get("reason"),"framework_status":p.get("framework_status"),"rr":p.get("rr")})
    st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)


def chat_page() -> None:
    header("💬 Bot Chat","Ask about the latest cycle, candidates, rejected signals, paper positions, P&L, or heartbeat. Answers are based on persisted Bot data; no data is invented.")
    question=st.chat_input("Ask the Bot… e.g. Why no BUY? What was the last cycle? How many candidates?")
    if question:
        q=question.lower(); s=status(); hb=heartbeat()
        if any(x in q for x in ("candidate","stock","buy","sell")):
            cs=pd.DataFrame(s.get("candidates",[])); answer=("No actionable candidates are recorded in the latest cycle. Rejections: "+str(s.get("rejections",{}))) if cs.empty else "Latest actionable candidates: "+", ".join(f"{r.symbol} ({r.decision})" for r in cs.itertuples())
        elif "reject" in q or "why no" in q or "no trade" in q: answer=f"Latest rejection funnel: {s.get('rejections',{})}. See Rejected Signals for symbol-level reasons."
        elif "p&l" in q or "profit" in q or "loss" in q: answer=f"Persisted realized P&L: ₹{float(s.get('realized_pnl',0) or 0):,.2f}; today's realized P&L: ₹{float(s.get('today_realized_pnl',0) or 0):,.2f}."
        elif "heartbeat" in q or "running" in q or "24/7" in q: answer=f"Latest persisted heartbeat: {hb.get('state','NOT FOUND')} at {hb.get('updated_at','—')}. Source: {hb.get('source','—')}."
        elif "cycle" in q or "refresh" in q: answer=f"Last cycle: {s.get('ended_at','—')}; duration {s.get('duration_seconds','—')}s; universe {s.get('stocks_observed',0)}; quotes {s.get('quotes',0)}; deep pool {s.get('deep_analysis_pool','—')}; errors {len(s.get('errors',[]))}."
        else: answer="I can answer from persisted Bot data about the latest cycle, candidates, rejection reasons, paper orders/P&L, and heartbeat."
        st.chat_message("user").write(question); st.chat_message("assistant").write(answer)


def live_charts() -> None:
    header("📉 Live Charts","Real Dhan history only.")
    try:mapping=json.loads(secret("DHAN_SECURITY_IDS_JSON","{}"))
    except Exception:mapping={}
    if not mapping: st.warning("DHAN_SECURITY_IDS_JSON is not configured."); return
    symbol=st.selectbox("Symbol",sorted(mapping)); tf=st.selectbox("Timeframe",[1,3,5,15,30,60],index=2)
    if st.button("Refresh live chart"):
        try:
            item=mapping[symbol]; sid=item.get("security_id",item) if isinstance(item,dict) else item; ex=item.get("exchange_segment","NSE_EQ") if isinstance(item,dict) else "NSE_EQ"; df=DhanBroker().history(str(sid),ex,tf)
            if df.empty: st.warning("DATA UNAVAILABLE"); return
            fig=go.Figure(go.Candlestick(x=df.timestamp,open=df.open,high=df.high,low=df.low,close=df.close,name=symbol)); st.plotly_chart(fig,use_container_width=True)
        except Exception as exc: st.error(f"LIVE DATA ERROR: {exc}")


def generic_page(page: str) -> None:
    if page in {"AI Baskets","Basket Detail","Stocks","Stock Screener","Stock Detail","360° Stock Analysis","Deep Research","Watchlist","Trend Scanner","Top Bullish","Top Bearish","Shift to Bullish","Shift to Bearish","Sector Analysis","Theme Analysis","Value Migration","Inflection Points","Value Chain","Profit Pool","SCRAP Analysis","Fundamental Analysis","Technical Analysis"}: research_page(page)
    elif page=="Paper Trading": paper_page()
    elif page=="Live Trading": live_page()
    elif page in {"Portfolio","Positions"}: portfolio_page()
    elif page=="P&L": pnl_page()
    elif page=="System Health": health_page()
    elif page=="Diagnostics": diagnostics_page()
    elif page=="Rejected Signals": rejected_page()
    elif page=="Live Charts": live_charts()
    else:
        header(page,"This page is wired to persisted records; it will not fabricate data when the corresponding backend source is unavailable.")
        df=sql("SELECT * FROM events ORDER BY id DESC LIMIT 200")
        st.dataframe(df,use_container_width=True,hide_index=True) if not df.empty else st.info("DATA UNAVAILABLE: no persisted records.")


def main() -> None:
    sync_remote_state()
    mode_switch(); controls(); page=st.sidebar.selectbox("Desk",PAGES,key="desk_page"); st.sidebar.markdown("---"); st.sidebar.caption("Always-on scheduler · 5-minute market-cycle interval · paper mode by default")
    if page=="Dashboard": dashboard()
    elif page=="New Chat": chat_page()
    elif page=="AI Prompt Guide": header(page,"AI remains advisory only. Missing data must be reported, never invented.")
    else: generic_page(page)


main()
