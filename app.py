from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from intraday_bot.brokers import DhanBroker
from intraday_bot.config import settings
from intraday_bot.database import Database
from intraday_bot.fundamentals_provider import fetch_fundamentals
from intraday_bot.research import FRAMEWORK_RULES, framework_analysis, fundamental_score, valuation_score
from intraday_bot.runtime import PAPER_MODE, run_cycle


st.set_page_config(page_title="NSE/BSE Intraday AI Trading Desk", layout="wide", initial_sidebar_state="expanded")
DB = Database()
ROOT = Path("data")
STATUS = ROOT / "monitor_status.json"
HB = ROOT / "worker_heartbeat.json"
SHB = ROOT / "scheduler_heartbeat.json"

PAGES = [
    "Dashboard", "AI Prompt Guide", "Deep Research", "Stock Screener", "360° Stock Analysis",
    "Trend Scanner", "Top Bullish", "Top Bearish", "Live Charts", "Portfolio", "Positions",
    "Orders", "Paper Trading", "P&L", "Trade Journal", "Rejected Signals", "System Health", "Settings",
]


def j(path):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}


def sql(q, p=()):
    try:
        with DB.connect() as c:
            return pd.DataFrame([dict(x) for x in c.execute(q, p).fetchall()])
    except Exception as e:
        st.error(f"DATABASE ERROR: {e}")
        return pd.DataFrame()


def events(kind=None, component=None, limit=1000):
    w, p = [], []
    if kind:
        w.append("event_type=?")
        p.append(kind)
    if component:
        w.append("component=?")
        p.append(component)
    where = " WHERE " + " AND ".join(w) if w else ""
    return sql(f"SELECT id,ts,component,severity,event_type,symbol,mode,payload FROM events{where} ORDER BY id DESC LIMIT ?", tuple(p + [limit]))


def flat(df):
    if df.empty:
        return df
    out = []
    for _, r in df.iterrows():
        try:
            x = json.loads(r.payload)
        except Exception:
            x = {"raw_payload": r.payload}
        if not isinstance(x, dict):
            x = {"raw_payload": x}
        z = {k: r[k] for k in ["id", "ts", "component", "severity", "event_type", "symbol", "mode"]}
        z.update(x)
        out.append(z)
    return pd.DataFrame(out)


def sync(force=False):
    now = time.time()
    last = st.session_state.get("sync", 0.0)
    if not force and now - last < 60:
        return bool(st.session_state.get("sync_ok", True))
    ok = True
    failures = []
    base = "https://raw.githubusercontent.com/kvrmsp18/stockmarket-bot/main/data/"
    for name, path in {"trading.db": ROOT / "trading.db", "monitor_status.json": STATUS, "worker_heartbeat.json": HB, "scheduler_heartbeat.json": SHB}.items():
        tmp = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            request = Request(base + name + f"?t={int(now * 1000)}", headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
            with urlopen(request, timeout=15) as response:
                data = response.read()
            fd, tmp = tempfile.mkstemp(dir=str(path.parent))
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except Exception as exc:
            ok = False
            failures.append(f"{name}: {exc}")
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
    st.session_state.sync = now
    st.session_state.sync_ok = ok
    st.session_state.sync_error = "; ".join(failures)
    st.session_state.sync_at = datetime.now(timezone.utc).isoformat()
    return ok


def heartbeat_age_seconds(payload):
    value = payload.get("updated_at") if isinstance(payload, dict) else None
    if not value:
        return float("inf")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return float("inf")


def header(t, d):
    st.title(t)
    st.info(d)


def dashboard():
    s = j(STATUS)
    h = j(HB)
    sh = j(SHB)
    header("📈 NSE/BSE Intraday AI Trading Desk", "Observe → Analyse → Filter → Rank → Decide → Validate → Size → Execute → Monitor → Exit → Reconcile. Paper mode is default; AI is advisory only.")
    a, b, c, d, e, f, g = st.columns(7)
    a.metric("Mode", s.get("mode", "PAPER"))
    b.metric("Universe", s.get("stocks_observed", 0))
    c.metric("Quotes", s.get("quotes", 0))
    d.metric("Candidates", len(s.get("candidates", [])))
    e.metric("Open Positions", s.get("positions_open", 0))
    f.metric("Today's P&L", f"₹{float(s.get('today_realized_pnl', 0) or 0):,.2f}")
    g.metric("Capital", f"₹{settings.reference_capital:,.0f}")
    cycle_errors = s.get("errors") or []
    age_seconds = heartbeat_age_seconds(h)
    market_open = bool(h.get("market_open", s.get("market_open", False)))
    cycle_success = h.get("cycle_success")
    if age_seconds > 900:
        st.error(f"TRADING MONITOR: OFFLINE · last heartbeat {h.get('updated_at', '—')} ({age_seconds / 60:.1f} min ago)")
    elif market_open and h.get("state") == "DEGRADED":
        detail = f" — {cycle_errors[0]}" if cycle_errors else f" — {h.get('message', 'cycle completed with errors')}"
        st.warning(f"TRADING ENGINE: DEGRADED · last cycle attempt {h.get('updated_at', '—')}{detail}")
    elif market_open and cycle_success is True:
        st.success(f"TRADING ENGINE: ONLINE · last successful market cycle {h.get('updated_at', '—')}")
    elif market_open:
        st.warning(f"TRADING ENGINE: NO VERIFIED SUCCESSFUL CYCLE · heartbeat {h.get('updated_at', '—')}")
    else:
        st.info(f"24/7 MONITOR: ACTIVE · NSE MARKET CLOSED · scheduler heartbeat {sh.get('updated_at', '—')}")
    st.write(f"**Scheduler:** {sh.get('state', 'NOT FOUND')} · {sh.get('updated_at', '—')} · market {'OPEN' if sh.get('market_open') else 'CLOSED'}")
    st.caption(f"Data sync: {'OK' if st.session_state.get('sync_ok', True) else 'FAILED'} · {st.session_state.get('sync_at', '—')}")
    if not st.session_state.get("sync_ok", True):
        st.error(f"Dashboard refresh could not retrieve latest persisted state: {st.session_state.get('sync_error', 'unknown error')}")
    cdf = pd.DataFrame(s.get("candidates", []))
    st.subheader("Actionable candidates")
    if cdf.empty:
        if cycle_errors:
            st.error(f"Latest cycle failed before producing candidates: {cycle_errors[0]}")
        else:
            st.warning("No actionable candidates persisted. Rejected Signals contains the exact reason.")
    else:
        st.dataframe(cdf, use_container_width=True, hide_index=True)


def prompt():
    header("🤖 AI Prompt Guide", "Operational AI contract. Missing data is reported, never invented; AI cannot override deterministic gates.")
    text = f'''NSE/BSE INTRADAY AI ADVISORY CONTRACT\n- Never invent missing data; report DATA UNAVAILABLE and missing fields.\n- AI is advisory only and cannot create/override an execution gate.\n- Deterministic market data, technical, SCRAP, funds, risk, broker and reconciliation controls always win.\n- Research frameworks: Buffett, Rakesh Jhunjhunwala, Peter Lynch, 100 Baggers, CANSLIM.\n- Bullish threshold: {settings.bullish_threshold}; bearish threshold: {settings.bearish_threshold}; minimum R:R: {settings.min_rr}.\n- Paper reference capital: ₹{settings.reference_capital:,.2f}.\n- Never expose credentials.'''
    st.code(text, language="text")
    st.download_button("Download Prompt_FINAL current", text, file_name="Prompt_FINAL_current.txt")
    for n, x in FRAMEWORK_RULES.items():
        st.write(f"**{n}:** {x['label']} — {', '.join(x['factors'])}")


def _framework_rows(bundle: dict) -> pd.DataFrame:
    rows = []
    for name, item in bundle.get("frameworks", {}).items():
        rows.append({"Framework": name, "Score": item.get("score", 0), "Confidence": item.get("confidence", 0), "Positive": ", ".join(item.get("positive_factors", [])) or "—", "Negative": ", ".join(item.get("negative_factors", [])) or "—", "Missing": ", ".join(item.get("missing_data", [])) or "—"})
    return pd.DataFrame(rows)


def frameworks():
    header("🧠 Five Research Frameworks", "The Bot's Deep Research layer. It uses source-backed company fundamentals with provider fallback; missing values remain DATA UNAVAILABLE and are never invented.")
    persisted = events("FRAMEWORK_ANALYSIS")
    persisted_symbols = sorted(persisted.symbol.dropna().astype(str).unique()) if not persisted.empty else []
    default_symbol = persisted_symbols[0] if persisted_symbols else "ABCAPITAL"
    symbol = st.text_input("NSE symbol", value=default_symbol, key="deep_research_symbol").strip().upper()

    c1, c2 = st.columns([1, 3])
    with c1:
        fetch_clicked = st.button("🔄 Refresh source-backed research", type="primary", key="refresh_deep_research")
    with c2:
        st.caption("Refreshes company-source data only. It does not place an order. Research remains advisory; deterministic execution and risk gates remain in force.")

    if fetch_clicked:
        if not symbol:
            st.warning("Enter an NSE symbol first.")
        else:
            with st.spinner(f"Fetching source fundamentals for {symbol}…"):
                try:
                    source = fetch_fundamentals(symbol)
                    research = {key: source[key] for key in ("profit_growth", "eps_growth", "roce", "roe", "debt_to_equity", "predictability", "earnings_quality", "pe", "sector_weight_pct", "company_weight_pct", "red_flags") if key in source}
                    bundle = framework_analysis(research)
                    snapshot = {"symbol": symbol, "source": source, "research_input": research, "fundamental_score": fundamental_score(research), "valuation_score": valuation_score(research), "frameworks": bundle, "fetched_at": datetime.now(timezone.utc).isoformat()}
                    st.session_state.deep_research_source = snapshot
                except Exception as exc:
                    st.error(str(exc))

    live = st.session_state.get("deep_research_source", {})
    if live and live.get("symbol") == symbol:
        source = live.get("source", {})
        bundle = live.get("frameworks", {})
        provider = source.get("provider", "unknown")
        status = source.get("source_status", "DATA UNAVAILABLE")
        if status == "AVAILABLE":
            st.success(f"SOURCE-BACKED DATA AVAILABLE · {source.get('source', provider)} · fetched {live.get('fetched_at', '—')}")
        elif status == "PARTIAL":
            st.warning(f"SOURCE-BACKED DATA PARTIAL · {source.get('source', provider)} · fetched {live.get('fetched_at', '—')}")
        else:
            st.error(f"SOURCE DATA UNAVAILABLE · {source.get('source', provider)}")
        fallback_reason = source.get("fallback_reason")
        if fallback_reason:
            st.caption("Twelve Data did not provide the requested data; the Bot automatically used the configured secondary provider.")
        metrics = {k: v for k, v in source.items() if k not in {"missing_provider_fields", "source_status", "symbol", "source", "provider", "fallback_reason", "endpoint_errors"}}
        if metrics:
            st.subheader("Source-backed company fundamentals")
            st.dataframe(pd.DataFrame(sorted(metrics.items()), columns=["Metric", "Value"]), use_container_width=True, hide_index=True)
        a, b, c = st.columns(3)
        a.metric("Fundamental Score", f"{float(live.get('fundamental_score', 0)):.2f}/10")
        b.metric("Valuation Score", f"{float(live.get('valuation_score', 0)):.2f}/10")
        c.metric("Research Status", bundle.get("status", "DATA UNAVAILABLE"))
        st.subheader("Framework evidence")
        st.dataframe(_framework_rows(bundle), use_container_width=True, hide_index=True)
        st.write(f"**Overall:** {bundle.get('overall', 0)} · **Agreement:** {bundle.get('agreement', 'DATA UNAVAILABLE')} · **Status:** {bundle.get('status', 'DATA UNAVAILABLE')}")
        missing = source.get("missing_provider_fields", [])
        if missing:
            st.warning("Provider did not supply: " + ", ".join(missing))
        with st.expander("Raw source-backed research evidence"):
            st.json(live)
        return

    if persisted_symbols:
        matching = persisted[persisted.symbol.astype(str) == symbol]
        if not matching.empty:
            row = matching.iloc[0]
            try:
                p = json.loads(row.payload)
            except Exception:
                p = {}
            f = p.get("frameworks", {})
            st.write(f"Last persisted research: {row.ts} · Overall: {f.get('overall', 'DATA UNAVAILABLE')} · Agreement: {f.get('agreement', 'DATA UNAVAILABLE')}")
            st.dataframe(_framework_rows(f), use_container_width=True, hide_index=True)
            with st.expander("Persisted evidence"):
                st.json(p)
            return
    st.info("No source-backed research loaded yet. Click **Refresh source-backed research** to retrieve the selected NSE company's fundamentals.")


def research_page(page):
    header(page, f"{page} — persisted Bot results only; no generic/fake stock data is substituted.")
    if page == "Deep Research":
        frameworks()
        return
    s = j(STATUS)
    c = pd.DataFrame(s.get("candidates", []))
    r = flat(events(component="research"))
    if page in {"Trend Scanner", "Top Bullish", "Top Bearish"}:
        if c.empty:
            st.warning("No actionable candidates in latest cycle.")
            return
        x = c.copy()
        x["_trend"] = pd.to_numeric(x.get("trend_score", 0), errors="coerce").fillna(0)
        if page == "Top Bullish": x = x[x._trend >= settings.bullish_threshold]
        if page == "Top Bearish": x = x[x._trend < settings.bearish_threshold]
        st.dataframe(x.sort_values("_trend", ascending=False).drop(columns=["_trend"]), use_container_width=True, hide_index=True)
        return
    if page == "Stock Screener":
        q = st.text_input("Filter", key="screener_filter")
        x = r if not q else r[r.astype(str).apply(lambda z: z.str.contains(q, case=False, na=False)).any(axis=1)]
        st.dataframe(x, use_container_width=True, hide_index=True)
        return
    if page == "360° Stock Analysis":
        syms = sorted(set(c.get("symbol", pd.Series(dtype=str)).dropna().astype(str)) | set(r.get("symbol", pd.Series(dtype=str)).dropna().astype(str)))
        if not syms:
            st.info("No stock records yet.")
            return
        sym = st.selectbox("Stock", syms, key="stock_360")
        if not c.empty and sym in set(c.symbol.astype(str)): st.json(c[c.symbol.astype(str) == sym].iloc[0].to_dict())
        if not r.empty: st.dataframe(r[r.symbol.astype(str) == sym], use_container_width=True, hide_index=True)
        frameworks()
        return
    st.info("No dedicated persisted dataset has been written for this view yet.")


def ledger(page):
    table = {"Orders": "orders", "Positions": "positions", "Trade Journal": "trades"}[page]
    df = sql(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 1000")
    header(page, f"Persisted {table} ledger.")
    if not df.empty: st.dataframe(df, use_container_width=True, hide_index=True)
    else: st.info(f"No {table} records yet.")


def pnl():
    df = sql("SELECT * FROM trades WHERE mode IN ('PAPER','LIVE_TEST') AND closed_at IS NOT NULL ORDER BY closed_at")
    header("💰 P&L", "Realized simulated-trade P&L including persisted charges.")
    if df.empty: st.info("No completed trades yet."); return
    df["net_pnl"] = pd.to_numeric(df["net_pnl"], errors="coerce").fillna(0)
    st.metric("Net P&L", f"₹{df.net_pnl.sum():,.2f}")
    st.dataframe(df, use_container_width=True, hide_index=True)


def charts():
    header("📉 Live Charts", "Real Dhan chart data only. Authentication uses the configured Dhan market-data credential.")
    try: m = json.loads(settings.dhan_security_ids_json or "{}")
    except Exception: m = {}
    if not m:
        st.warning("DHAN_SECURITY_IDS_JSON is not configured."); return
    sym = st.selectbox("Symbol", sorted(m), key="chart_symbol")
    tf = st.selectbox("Minutes", [1, 3, 5, 15, 30, 60], index=2, key="chart_tf")
    if st.button("Refresh live chart", key="chart_refresh"):
        try:
            i = m[sym]; sid = i.get("security_id", i) if isinstance(i, dict) else i; ex = i.get("exchange_segment", "NSE_EQ") if isinstance(i, dict) else "NSE_EQ"
            df = DhanBroker().history(str(sid), ex, tf)
            if df.empty: st.warning("DATA UNAVAILABLE"); return
            fig = go.Figure(go.Candlestick(x=df.timestamp, open=df.open, high=df.high, low=df.low, close=df.close)); fig.update_layout(xaxis_rangeslider_visible=False); st.plotly_chart(fig, use_container_width=True)
        except Exception as e: st.error(f"LIVE DATA ERROR: {e}")


def main():
    sync(); st.session_state.mode = PAPER_MODE; st.sidebar.success("🟢 PAPER MODE — SIMULATED ORDERS")
    if st.sidebar.button("🔄 Refresh Dashboard", key="refresh_button"): sync(force=True); st.rerun()
    if not st.session_state.get("sync_ok", True): st.sidebar.error("State sync failed — dashboard may be stale")
    else: st.sidebar.caption(f"State sync OK · {st.session_state.get('sync_at', '—')}")
    if st.sidebar.button("▶ Run Analysis", type="primary", key="run_analysis"):
        current = datetime.now(timezone.utc); ist_now = current.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Kolkata"))
        is_market_open = ist_now.weekday() < 5 and (ist_now.hour, ist_now.minute, ist_now.second) >= (9, 15, 0) and (ist_now.hour, ist_now.minute, ist_now.second) <= (15, 30, 0)
        if not is_market_open:
            x = {"mode": PAPER_MODE, "market_open": False, "stocks_observed": j(STATUS).get("stocks_observed", 0), "quotes": j(STATUS).get("quotes", 0), "candidates": [], "orders": [], "errors": [], "execution_gate": "MARKET_CLOSED", "message": "NSE market is closed. Manual analysis is available during market hours; the 24/7 monitor remains active outside market hours."}
            st.session_state.last_manual_cycle = x; st.info(x["message"])
        else:
            with st.spinner("Running full deterministic paper cycle…"):
                try:
                    x = run_cycle(); st.session_state.last_manual_cycle = x
                except Exception as e: st.error(f"RUN ERROR: {e}")
    nav = st.sidebar.selectbox("Desk", PAGES, index=PAGES.index(st.session_state.get("page", "Dashboard")))
    st.session_state.page = nav
    if nav == "Dashboard": dashboard()
    elif nav == "AI Prompt Guide": prompt()
    elif nav in {"Deep Research", "Stock Screener", "360° Stock Analysis", "Trend Scanner", "Top Bullish", "Top Bearish"}: research_page(nav)
    elif nav in {"Orders", "Positions", "Trade Journal"}: ledger(nav)
    elif nav == "P&L": pnl()
    elif nav == "Live Charts": charts()
    else: st.info(f"{nav} view is available for the persisted paper-trading system.")


main()
