from __future__ import annotations
import json, os, tempfile, time
from pathlib import Path
from urllib.request import Request, urlopen
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from intraday_bot.brokers import DhanBroker
from intraday_bot.config import settings
from intraday_bot.database import Database
from intraday_bot.research import FRAMEWORK_RULES
from intraday_bot.runtime import LIVE_TEST_MODE, PAPER_MODE, run_cycle

st.set_page_config(page_title="NSE/BSE Intraday AI Trading Desk", layout="wide", initial_sidebar_state="expanded")
DB=Database(); ROOT=Path("data"); STATUS=ROOT/"monitor_status.json"; HB=ROOT/"worker_heartbeat.json"; SHB=ROOT/"scheduler_heartbeat.json"
PAGES=["Dashboard","New Chat","AI Prompt Guide","AI Baskets","Basket Detail","Stock Screener","Stocks","Stock Detail","360° Stock Analysis","Deep Research","Watchlist","Trend Scanner","Top Bullish","Top Bearish","Shift to Bullish","Shift to Bearish","Sector Analysis","Theme Analysis","Value Migration","Inflection Points","Value Chain","Profit Pool","SCRAP Analysis","Fundamental Analysis","Technical Analysis","Live Charts","Portfolio","Positions","Orders","Paper Trading","Live Trading","P&L","Trade Journal","Rejected Signals","Backtesting","Bot Performance","News","System Health","Diagnostics","Settings"]

def j(path):
    try:return json.loads(path.read_text()) if path.exists() else {}
    except:return {}

def sql(q,p=()):
    try:
        with DB.connect() as c:return pd.DataFrame([dict(x) for x in c.execute(q,p).fetchall()])
    except Exception as e: st.error(f"DATABASE ERROR: {e}"); return pd.DataFrame()

def events(kind=None,component=None,limit=1000):
    w=[]; p=[]
    if kind:w.append("event_type=?");p.append(kind)
    if component:w.append("component=?");p.append(component)
    where=" WHERE "+" AND ".join(w) if w else ""
    return sql(f"SELECT id,ts,component,severity,event_type,symbol,mode,payload FROM events{where} ORDER BY id DESC LIMIT ?",tuple(p+[limit]))

def flat(df):
    if df.empty:return df
    out=[]
    for _,r in df.iterrows():
        try:x=json.loads(r.payload)
        except:x={"raw_payload":r.payload}
        if not isinstance(x,dict):x={"raw_payload":x}
        z={k:r[k] for k in ["id","ts","component","severity","event_type","symbol","mode"]};z.update(x);out.append(z)
    return pd.DataFrame(out)

def sync(force=False):
    now=time.time(); last=st.session_state.get("sync",0)
    if not force and now-last<60:return True
    ok=True; base="https://raw.githubusercontent.com/kvrmsp18/stockmarket-bot/main/data/"
    for name,path in {"trading.db":ROOT/"trading.db","monitor_status.json":STATUS,"worker_heartbeat.json":HB,"scheduler_heartbeat.json":SHB}.items():
        tmp=None
        try:
            path.parent.mkdir(parents=True,exist_ok=True);r=Request(base+name+f"?t={int(now*1000)}",headers={"Cache-Control":"no-cache"})
            with urlopen(r,timeout=15) as x:data=x.read()
            fd,tmp=tempfile.mkstemp(dir=str(path.parent))
            with os.fdopen(fd,"wb") as f:f.write(data)
            os.replace(tmp,path)
        except Exception:ok=False
        finally:
            if tmp and os.path.exists(tmp):
                try:os.unlink(tmp)
                except:pass
    st.session_state.sync=now;return ok

def header(t,d):st.title(t);st.info(d)

def dashboard():
    s=j(STATUS);h=j(HB);sh=j(SHB);header("📈 NSE/BSE Intraday AI Trading Desk","Observe → Analyse → Filter → Rank → Decide → Validate → Size → Execute → Monitor → Exit → Reconcile. Paper mode is default; AI is advisory only.")
    a,b,c,d,e,f,g=st.columns(7);a.metric("Mode",s.get("mode","PAPER"));b.metric("Universe",s.get("stocks_observed",0));c.metric("Quotes",s.get("quotes",0));d.metric("Candidates",len(s.get("candidates",[])));e.metric("Open Positions",s.get("positions_open",0));f.metric("Today's P&L",f"₹{float(s.get('today_realized_pnl',0) or 0):,.2f}");g.metric("Capital",f"₹{settings.reference_capital:,.0f}")
    cycle_errors=s.get("errors") or []
    age_seconds=(time.time()-time.mktime(time.strptime(h.get("updated_at","")[:19],"%Y-%m-%dT%H:%M:%S"))) if h.get("updated_at") else 10**9
    if h.get("state")=="ERROR":
        st.error(f"TRADING ENGINE ERROR: {h.get('message','Unknown error')} · last heartbeat {h.get('updated_at','—')}")
    elif age_seconds>900:
        st.error(f"TRADING ENGINE: OFFLINE · last real cycle heartbeat {h.get('updated_at','—')} ({age_seconds/60:.1f} min ago)")
    elif h.get("state")=="DEGRADED":
        detail=f" — {cycle_errors[0]}" if cycle_errors else f" — {h.get('message','cycle completed with errors')}"
        st.warning(f"TRADING ENGINE: DEGRADED · last cycle attempt {h.get('updated_at','—')}{detail}")
    else:
        st.success(f"TRADING ENGINE: ONLINE · last successful market cycle {h.get('updated_at','—')}")
    st.write(f"**Scheduler:** {sh.get('state','NOT FOUND')} · {sh.get('updated_at','—')} · market {'OPEN' if sh.get('market_open') else 'CLOSED'}")
    cdf=pd.DataFrame(s.get("candidates",[])); st.subheader("Actionable candidates")
    if cdf.empty:
        if cycle_errors: st.error(f"Latest cycle failed before producing candidates: {cycle_errors[0]}")
        else: st.warning("No actionable candidates persisted. Diagnostics and Rejected Signals show the exact reason.")
    else: st.dataframe(cdf,use_container_width=True,hide_index=True)

def prompt():
    header("🤖 AI Prompt Guide","Operational AI contract. Missing data is reported, never invented; AI cannot override deterministic gates.")
    text=f'''NSE/BSE INTRADAY AI ADVISORY CONTRACT\n- Never invent missing data; report DATA UNAVAILABLE and missing fields.\n- AI is advisory only and cannot create/override an execution gate.\n- Deterministic market data, technical, SCRAP, funds, risk, broker and reconciliation controls always win.\n- Research frameworks: Buffett, Rakesh Jhunjhunwala, Peter Lynch, 100 Baggers, CANSLIM.\n- Bullish threshold: {settings.bullish_threshold}; bearish threshold: {settings.bearish_threshold}; minimum R:R: {settings.min_rr}.\n- Paper reference capital: ₹{settings.reference_capital:,.2f}.\n- Never expose credentials.'''
    st.code(text,language="text");st.download_button("Download Prompt_FINAL current",text,file_name="Prompt_FINAL_current.txt")
    for n,x in FRAMEWORK_RULES.items():st.write(f"**{n}:** {x['label']} — {', '.join(x['factors'])}")

def frameworks():
    header("🧠 Five Research Frameworks","Each framework is shown separately with score, confidence, evidence and missing data. These are not standalone intraday triggers.")
    df=events("FRAMEWORK_ANALYSIS")
    if df.empty:st.warning("No FRAMEWORK_ANALYSIS records persisted yet.");return
    syms=sorted(df.symbol.dropna().astype(str).unique());sym=st.selectbox("Stock",syms,key="framework_stock");row=df[df.symbol.astype(str)==sym].iloc[0]
    try:p=json.loads(row.payload)
    except:p={}
    f=p.get("frameworks",{});st.write(f"Last research: {row.ts} · Overall: {f.get('overall','DATA UNAVAILABLE')} · Agreement: {f.get('agreement','DATA UNAVAILABLE')}")
    rows=[]
    for n,x in f.get("frameworks",{}).items():rows.append({"Framework":n,"Score":x.get("score",0),"Confidence":x.get("confidence",0),"Positive":", ".join(x.get("positive_factors",[])) or "—","Negative":", ".join(x.get("negative_factors",[])) or "—","Missing":", ".join(x.get("missing_data",[])) or "—"})
    st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
    with st.expander("Raw evidence"):st.json(p)

def research_page(page):
    header(page,f"{page} — persisted Bot results only; no generic/fake stock data is substituted.")
    if page in {"AI Baskets","Basket Detail","Deep Research"}:frameworks();return
    s=j(STATUS);c=pd.DataFrame(s.get("candidates",[]));r=flat(events(component="research"))
    if page in {"Trend Scanner","Top Bullish","Top Bearish","Shift to Bullish","Shift to Bearish"}:
        if c.empty:st.warning("No actionable candidates in latest cycle.");return
        x=c.copy();x["_trend"]=pd.to_numeric(x.get("trend_score",0),errors="coerce").fillna(0)
        if page in {"Top Bullish","Shift to Bullish"}:x=x[x._trend>=settings.bullish_threshold]
        if page in {"Top Bearish","Shift to Bearish"}:x=x[x._trend<settings.bearish_threshold]
        st.dataframe(x.sort_values("_trend",ascending=False).drop(columns=["_trend"]),use_container_width=True,hide_index=True);return
    if page=="Stock Screener":
        q=st.text_input("Filter",key="screener_filter");x=r if not q else r[r.astype(str).apply(lambda z:z.str.contains(q,case=False,na=False)).any(axis=1)];st.dataframe(x,use_container_width=True,hide_index=True);return
    if page in {"Stocks","Stock Detail","360° Stock Analysis"}:
        syms=sorted(set(c.get("symbol",pd.Series(dtype=str)).dropna().astype(str))|set(r.get("symbol",pd.Series(dtype=str)).dropna().astype(str)))
        if not syms:st.info("No stock records yet.");return
        sym=st.selectbox("Stock",syms,key=f"{page}_stock")
        if not c.empty and sym in set(c.symbol.astype(str)):st.json(c[c.symbol.astype(str)==sym].iloc[0].to_dict())
        if not r.empty:st.dataframe(r[r.symbol.astype(str)==sym],use_container_width=True,hide_index=True)
        if page=="360° Stock Analysis":frameworks()
        return
    if page in {"Sector Analysis","Theme Analysis","Value Migration","Inflection Points","Value Chain","Profit Pool"}:
        col="sector" if page=="Sector Analysis" else "theme"
        if c.empty or col not in c:st.info(f"No persisted {col} dimension is available yet.");return
        st.dataframe(c.groupby(col,dropna=False).agg(candidates=("symbol","count"),avg_trend=("trend_score","mean")).reset_index(),use_container_width=True,hide_index=True);return
    if page in {"SCRAP Analysis","Fundamental Analysis","Technical Analysis","Watchlist"}:st.dataframe(r if not r.empty else c,use_container_width=True,hide_index=True);return
    st.info("No dedicated persisted dataset has been written for this view yet.")

def ledger(page):
    table={"Orders":"orders","Positions":"positions","Trade Journal":"trades"}[page];df=sql(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 1000");header(page,f"Persisted {table} ledger.");st.dataframe(df,use_container_width=True,hide_index=True) if not df.empty else st.info(f"No {table} records yet.")

def pnl():
    df=sql("SELECT * FROM trades WHERE mode IN ('PAPER','LIVE_TEST') AND closed_at IS NOT NULL ORDER BY closed_at");header("💰 P&L","Realized simulated-trade P&L including persisted charges.")
    if df.empty:st.info("No completed trades yet.");return
    df["net_pnl"]=pd.to_numeric(df["net_pnl"],errors="coerce").fillna(0);st.metric("Net P&L",f"₹{df.net_pnl.sum():,.2f}");st.dataframe(df,use_container_width=True,hide_index=True)

def charts():
    header("📉 Live Charts","Real Dhan chart data only. Authentication uses DHAN_API_KEY.")
    try:m=json.loads(settings.dhan_security_ids_json or "{}")
    except:m={}
    if not m:st.warning("DHAN_SECURITY_IDS_JSON is not configured.");return
    sym=st.selectbox("Symbol",sorted(m),key="chart_symbol");tf=st.selectbox("Minutes",[1,3,5,15,30,60],index=2,key="chart_tf")
    if st.button("Refresh live chart",key="chart_refresh"):
        try:
            i=m[sym];sid=i.get("security_id",i) if isinstance(i,dict) else i;ex=i.get("exchange_segment","NSE_EQ") if isinstance(i,dict) else "NSE_EQ";df=DhanBroker().history(str(sid),ex,tf)
            if df.empty:st.warning("DATA UNAVAILABLE");return
            fig=go.Figure(go.Candlestick(x=df.timestamp,open=df.open,high=df.high,low=df.low,close=df.close));fig.update_layout(xaxis_rangeslider_visible=False);st.plotly_chart(fig,use_container_width=True)
        except Exception as e:st.error(f"LIVE DATA ERROR: {e}")

def main():
    sync();mode=st.sidebar.radio("Mode",["PAPER TRADING","LIVE TRADING"],index=0,key="mode_radio");st.session_state.mode=PAPER_MODE if mode.startswith("PAPER") else LIVE_TEST_MODE
    st.sidebar.success("🟢 PAPER MODE — SIMULATED ORDERS" if st.session_state.mode==PAPER_MODE else "🟠 LIVE TEST — NO LIVE ORDERS")
    if st.sidebar.button("🔄 Refresh Search / Dashboard",key="refresh_button"):
        sync(force=True)
        st.rerun()
    if st.sidebar.button("▶ Run Analysis",type="primary",key="run_analysis"):
        x=run_cycle(st.session_state.mode);st.sidebar.write(f"Cycle complete · {len(x.get('candidates',[]))} candidates · {len(x.get('errors',[]))} errors");st.rerun()
    page=st.sidebar.selectbox("Desk",PAGES,key="desk_page")
    if page=="Dashboard":dashboard()
    elif page=="New Chat":
        header("💬 Bot Chat","Persisted Bot-data answers only.");q=st.chat_input("Ask about candidates, P&L, rejections or heartbeat")
        if q:
            s=j(STATUS);h=j(HB);c=pd.DataFrame(s.get("candidates",[]));x=q.lower()
            if "heartbeat" in x or "running" in x:answer=f"Trading heartbeat: {h.get('state','NOT FOUND')} at {h.get('updated_at','—')}."
            elif "reject" in x or "no trade" in x:answer=f"Rejection funnel: {s.get('rejections',{})}. See Rejected Signals for symbol-level evidence."
            elif "p&l" in x or "profit" in x or "loss" in x:answer=f"Today's persisted realized P&L: ₹{float(s.get('today_realized_pnl',0) or 0):,.2f}."
            elif "candidate" in x or "buy" in x or "sell" in x:answer="No actionable candidates are persisted in the latest cycle." if c.empty else "Latest candidates: "+", ".join(f"{r.symbol} ({r.decision})" for r in c.itertuples())
            else:answer=f"Last cycle: {s.get('ended_at','—')}; universe {s.get('stocks_observed',0)}; quotes {s.get('quotes',0)}; errors {len(s.get('errors',[]))}."
            st.chat_message("user").write(q);st.chat_message("assistant").write(answer)
    elif page=="AI Prompt Guide":prompt()
    elif page in {"Orders","Positions","Trade Journal"}:ledger(page)
    elif page=="P&L":pnl()
    elif page=="Live Charts":charts()
    elif page=="Rejected Signals":
        header(page,"Symbol-level rejection evidence.");x=flat(events("SIGNAL_REJECTED"));st.dataframe(x,use_container_width=True,hide_index=True) if not x.empty else st.info("No rejected signals persisted yet.")
    elif page=="System Health":
        header(page,"Scheduler, worker, Dhan authentication and latest cycle state.");d=DhanBroker().health();st.json({"worker":j(HB),"scheduler":j(SHB),"dhan_authenticated":d.authenticated,"dhan_message":d.message,"credential_source":settings.dhan_market_data_credential_source,"reference_capital":settings.reference_capital,"status":j(STATUS)})
    elif page=="Diagnostics":
        header(page,"Persisted troubleshooting events.");st.json(j(STATUS));x=sql("SELECT * FROM events WHERE severity IN ('ERROR','WARN') OR event_type NOT IN ('CYCLE_START','CYCLE_END') ORDER BY id DESC LIMIT 1000");st.dataframe(x,use_container_width=True,hide_index=True) if not x.empty else st.info("No diagnostic events persisted.")
    elif page=="Settings":
        header(page,"Effective non-secret configuration.");st.dataframe(pd.DataFrame(list({"reference_capital":settings.reference_capital,"min_rr":settings.min_rr,"bullish_threshold":settings.bullish_threshold,"bearish_threshold":settings.bearish_threshold,"max_trades_per_day":settings.max_trades_per_day,"emergency_stop":settings.emergency_stop,"Dhan credential source":settings.dhan_market_data_credential_source,"Dhan credential configured":bool(settings.dhan_market_data_token)}.items()),columns=["Setting","Value"]),use_container_width=True,hide_index=True)
    elif page=="Backtesting":
        header(page,"Historical backtest results only.");x=sql("SELECT * FROM cycles ORDER BY started_at DESC LIMIT 1000");st.dataframe(x,use_container_width=True,hide_index=True) if not x.empty else st.info("No persisted backtest/cycle results yet.")
    elif page=="Bot Performance":
        df=sql("SELECT * FROM trades WHERE mode IN ('PAPER','LIVE_TEST') AND closed_at IS NOT NULL ORDER BY closed_at");header(page,"Persisted trade performance.");st.dataframe(df,use_container_width=True,hide_index=True) if not df.empty else st.info("No completed trades yet.")
    elif page=="News":
        header(page,"Persisted news events only.");x=flat(events());x=x[x.event_type.astype(str).str.contains("NEWS",case=False,na=False)] if not x.empty else x;st.dataframe(x,use_container_width=True,hide_index=True) if not x.empty else st.info("No persisted news events yet.")
    elif page=="Live Trading":
        header(page,"LIVE TEST ONLY — no real broker order submission.");st.warning("Actual live execution remains disabled.");x=sql("SELECT * FROM orders WHERE order_id LIKE 'LIVETEST-%' ORDER BY ts DESC");st.dataframe(x,use_container_width=True,hide_index=True) if not x.empty else st.info("No live-test orders yet.")
    elif page=="Paper Trading":
        header(page,"Real Dhan market data + simulated execution.");x=sql("SELECT * FROM orders WHERE order_id LIKE 'PAPER-%' ORDER BY ts DESC");st.dataframe(x,use_container_width=True,hide_index=True) if not x.empty else st.info("No paper orders yet.")
    elif page=="Portfolio":
        df=sql("SELECT * FROM positions ORDER BY opened_at DESC");header(page,"Persisted positions and exposure.");st.dataframe(df,use_container_width=True,hide_index=True) if not df.empty else st.info("No positions yet.")
    else:research_page(page)
main()
