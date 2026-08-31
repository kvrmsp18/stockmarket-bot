from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from intraday_bot.database import Database
from intraday_bot.brokers import DhanBroker

st.set_page_config(
    page_title="NSE/BSE Intraday AI Trading Desk",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB = Database()
STATUS = Path("data/monitor_status.json")

PAGES = [
    "Dashboard", "New Chat", "AI Prompt Guide", "AI Baskets", "Basket Detail",
    "Stock Screener", "Stocks", "Stock Detail", "360° Stock Analysis", "Deep Research",
    "Watchlist", "Trend Scanner", "Top Bullish", "Top Bearish", "Shift to Bullish",
    "Shift to Bearish", "Sector Analysis", "Theme Analysis", "Value Migration",
    "Inflection Points", "Value Chain", "Profit Pool", "SCRAP Analysis",
    "Fundamental Analysis", "Technical Analysis", "Live Charts", "Portfolio",
    "Positions", "Orders", "Paper Trading", "Live Trading", "P&L", "Trade Journal",
    "Rejected Signals", "Backtesting", "Bot Performance", "News", "System Health",
    "Diagnostics", "Settings",
]


def secret(name: str, default: str = "") -> str:
    value = os.getenv(name, "")
    if value:
        return value
    try:
        value = st.secrets.get(name, default)
    except Exception:
        value = default
    return str(value or "")


def status() -> dict[str, Any]:
    if not STATUS.exists():
        return {}
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def records(table: str, limit: int = 200) -> pd.DataFrame:
    try:
        rows = DB.recent(table, limit)
    except Exception as exc:
        st.error(f"DATABASE ERROR: {exc}")
        return pd.DataFrame()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def mode_switch() -> None:
    st.sidebar.markdown("### Trading Mode")
    st.sidebar.success("🟢 PAPER MODE — DEFAULT")
    st.sidebar.radio(
        "Mode",
        ["PAPER TRADING", "LIVE TRADING"],
        index=0,
        disabled=True,
        help="Live trading remains hard-disabled until the project's explicit safety activation is completed.",
    )
    st.sidebar.caption("LIVE TRADING: 🔴 DISABLED")


def header(title: str, description: str | None = None) -> None:
    st.title(title)
    if description:
        st.info(description)


def dashboard() -> None:
    s = status()
    header(
        "📈 NSE/BSE Intraday AI Trading Desk",
        "The backend runs independently of this UI. Paper trading is the active mode; live trading is hard-disabled by default.",
    )
    a, b, c, d, e, f = st.columns(6)
    a.metric("Mode", "PAPER")
    b.metric("Universe", s.get("stocks_observed", 0))
    c.metric("Quotes", s.get("quotes", 0))
    d.metric("Candidates", len(s.get("candidates", [])))
    e.metric("Open Positions", s.get("positions_open", 0))
    f.metric("Realized P&L", f"₹{s.get('realized_pnl', 0):,.2f}")

    st.subheader("Market / Execution Health")
    h1, h2, h3, h4 = st.columns(4)
    h1.write("**Market:** " + ("OPEN" if s.get("market_open") else "CLOSED"))
    h2.write("**Data:** " + ("HEALTHY" if s.get("quotes") else "DATA UNAVAILABLE"))
    h3.write("**Dhan:** " + ("CONNECTED" if secret("DHAN_ACCESS_TOKEN") else "NOT CONFIGURED"))
    h4.write("**AI:** " + ("CONFIGURED" if secret("OPENAI_API_KEY") or secret("ANTHROPIC_API_KEY") else "ADVISORY UNAVAILABLE"))
    st.write(
        f"**Last cycle:** {s.get('ended_at', 'Not available')} · "
        f"**Duration:** {s.get('duration_seconds', 0)}s · "
        f"**Execution gate:** {s.get('execution_gate', 'Not evaluated')}"
    )

    st.subheader("Top Intraday Candidates")
    candidates = s.get("candidates", [])
    if candidates:
        st.dataframe(pd.DataFrame(candidates), use_container_width=True, hide_index=True)
    else:
        st.warning("No actionable candidates were recorded in the latest cycle. Check System Health/Diagnostics for DATA_UNAVAILABLE or rejection reasons.")
    if s.get("errors"):
        st.error("\n".join(str(x) for x in s["errors"][:10]))


def signals_page(title: str = "Signals") -> None:
    header(title, "Showing persisted signals from the production SQLite journal. No synthetic market values are created.")
    df = records("signals", 1000)
    if df.empty:
        st.warning("No persisted signals are available yet.")
        return
    st.dataframe(df, use_container_width=True, hide_index=True)


def screener() -> None:
    header("🔎 Stock Screener", "The screener uses candidates persisted by the latest backend cycle.")
    s = status()
    rows = s.get("candidates", [])
    if not rows:
        st.info("No actionable candidates in the latest persisted cycle.")
        return
    df = pd.DataFrame(rows)
    q = st.text_input("Search symbol")
    if q:
        df = df[df["symbol"].astype(str).str.contains(q.upper(), na=False)]
    st.dataframe(df, use_container_width=True, hide_index=True)


def stock_detail(title: str = "📊 Stock Detail") -> None:
    header(title, "Detailed view of a symbol only when the backend has persisted candidate data.")
    rows = status().get("candidates", [])
    if not rows:
        st.info("No candidate symbols are available in the latest cycle.")
        return
    symbols = sorted({str(x.get("symbol")) for x in rows if x.get("symbol")})
    symbol = st.selectbox("Stock", symbols)
    row = next(x for x in rows if x.get("symbol") == symbol)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Decision", row.get("decision", "—"))
    c2.metric("Trend Score", f"{float(row.get('trend_score', 0) or 0):.2f}/10")
    c3.metric("R:R", f"{float(row.get('rr', 0) or 0):.2f}")
    c4.metric("Quantity", row.get("quantity", 0))
    st.json(row)


def live_charts() -> None:
    header("📉 Live Market Charts", "Charts use Dhan market data when credentials and a security mapping are available. No synthetic data is labelled live.")
    try:
        mapping = json.loads(secret("DHAN_SECURITY_IDS_JSON", "{}"))
    except Exception:
        mapping = {}
    if not isinstance(mapping, dict) or not mapping:
        st.warning("DHAN_SECURITY_IDS_JSON is not configured; live chart data is unavailable.")
        return
    symbols = sorted(mapping)
    symbol = st.selectbox("Symbol", symbols)
    tf = st.selectbox("Timeframe (minutes)", [1, 3, 5, 15, 30, 60], index=2)
    if st.button("Refresh live chart"):
        try:
            item = mapping[symbol]
            sid = item.get("security_id", item) if isinstance(item, dict) else item
            exchange = item.get("exchange_segment", "NSE_EQ") if isinstance(item, dict) else "NSE_EQ"
            df = DhanBroker().history(str(sid), exchange, tf)
            if df.empty:
                st.warning("DATA UNAVAILABLE for the selected symbol/timeframe.")
                return
            fig = go.Figure(go.Candlestick(x=df.timestamp, open=df.open, high=df.high, low=df.low, close=df.close, name=symbol))
            if "volume" in df.columns:
                fig.add_trace(go.Bar(x=df.timestamp, y=df.volume, name="Volume", yaxis="y2"))
                fig.update_layout(yaxis2=dict(title="Volume", overlaying="y", side="right", showgrid=False))
            fig.update_layout(height=650, title=f"{symbol} · {tf} minute", yaxis=dict(title="Price"))
            st.plotly_chart(fig, use_container_width=True)
        except Exception as exc:
            st.error(f"LIVE DATA ERROR: {exc}")


def positions_page() -> None:
    header("📌 Positions / Portfolio", "Persisted position state from the paper-trading database.")
    df = records("positions", 1000)
    if df.empty:
        st.info("No positions are currently persisted.")
        return
    st.dataframe(df, use_container_width=True, hide_index=True)


def orders_page() -> None:
    header("🧾 Orders", "Persisted paper/live order records. Live trading remains disabled by the application safety gate.")
    df = records("orders", 1000)
    if df.empty:
        st.info("No orders are currently persisted.")
        return
    st.dataframe(df, use_container_width=True, hide_index=True)


def trades_page(title: str = "📈 P&L / Trade Journal / Bot Performance") -> None:
    header(title, "Calculated only from persisted completed trades.")
    df = records("trades", 1000)
    if df.empty:
        st.info("No completed trades yet. Paper validation will populate this table after positions are opened and exited.")
        return
    if "closed_at" in df.columns:
        df["closed_at"] = pd.to_datetime(df["closed_at"], errors="coerce")
        df = df.sort_values("closed_at")
    df["net_pnl"] = pd.to_numeric(df["net_pnl"], errors="coerce").fillna(0)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Trades", len(df))
    c2.metric("Net P&L", f"₹{df.net_pnl.sum():,.2f}")
    c3.metric("Win Rate", f"{(df.net_pnl > 0).mean() * 100:.1f}%")
    losses = abs(df.loc[df.net_pnl < 0, "net_pnl"].sum())
    wins = df.loc[df.net_pnl > 0, "net_pnl"].sum()
    c4.metric("Profit Factor", f"{wins / losses:.2f}" if losses else "∞")
    if "closed_at" in df.columns:
        st.line_chart(df.set_index("closed_at")["net_pnl"].cumsum())
    st.dataframe(df, use_container_width=True, hide_index=True)


def events_page(title: str = "🩺 System Health / Diagnostics") -> None:
    header(title, "Operational evidence from the persisted monitor journal and latest cycle status.")
    s = status()
    if s:
        st.subheader("Latest monitor status")
        st.json(s)
    df = records("events", 500)
    if df.empty:
        st.info("No persisted events are available.")
    else:
        st.subheader("Recent events")
        st.dataframe(df, use_container_width=True, hide_index=True)


def prompt_guide() -> None:
    header("🤖 AI Prompt Guide", "AI remains advisory only. This page does not fabricate an AI result when no provider is configured.")
    st.markdown("""
### Safe AI workflow
1. Supply only persisted market, technical, risk and portfolio facts.
2. Require explicit assumptions and evidence.
3. AI may rank or explain candidates, but cannot override funds, risk, execution or safety gates.
4. Missing external data must be reported as **DATA UNAVAILABLE**.
5. Paper trading remains the default execution mode.
""")
    st.code("Analyse the supplied persisted market facts. Do not invent missing prices, news, fundamentals or broker responses. Return evidence, risks, invalidation conditions and an advisory ranking only.", language="text")


def chat_page() -> None:
    header("💬 New Chat", "Advisory chat is available only when an AI provider is configured; otherwise the application remains truthful about unavailable AI.")
    api_configured = bool(secret("OPENAI_API_KEY") or secret("ANTHROPIC_API_KEY"))
    if not api_configured:
        st.warning("AI PROVIDER NOT CONFIGURED. Add the appropriate provider secret to enable advisory chat.")
        return
    st.info("AI provider credentials are configured. The existing project AI layer should be used for provider calls; this page does not bypass risk or execution gates.")
    question = st.text_area("Question")
    if question:
        st.write("Question received:")
        st.write(question)
        st.caption("Provider execution is intentionally not duplicated in the UI layer.")


def research_placeholder(title: str) -> None:
    header(title, "This page is wired to the production journal but will not manufacture research without a configured source.")
    df = records("events", 200)
    if df.empty:
        st.warning("DATA UNAVAILABLE: no persisted research/source events are available.")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)


def news_page() -> None:
    header("📰 News", "News is shown only when the backend has persisted source events. No fabricated headlines are displayed.")
    df = records("events", 500)
    if df.empty:
        st.warning("DATA UNAVAILABLE: no persisted news/source events are available.")
    else:
        if "event_type" in df.columns:
            mask = df["event_type"].astype(str).str.contains("news|headline", case=False, na=False)
            filtered = df[mask]
            st.dataframe(filtered if not filtered.empty else df, use_container_width=True, hide_index=True)


def rejected_page() -> None:
    header("⛔ Rejected Signals", "Rejected candidates are derived from persisted signal/event records; no rejection is invented.")
    df = records("events", 1000)
    if df.empty:
        st.info("No persisted rejection events are available.")
        return
    if "event_type" in df.columns:
        mask = df["event_type"].astype(str).str.contains("reject|rejection|gate|risk", case=False, na=False)
        filtered = df[mask]
        st.dataframe(filtered if not filtered.empty else df, use_container_width=True, hide_index=True)


def settings_page() -> None:
    header("⚙️ Settings", "Read-only runtime configuration. Secrets are never displayed.")
    names = [
        "BOT_MODE", "DHAN_CLIENT_ID", "DHAN_LIVE_TRADING_ENABLED", "RISK_PER_TRADE_PCT",
        "MAX_DAILY_LOSS", "MAX_OPEN_POSITIONS", "MAX_POSITION_EXPOSURE", "MAX_SECTOR_EXPOSURE",
        "MIN_RR", "DATA_FRESHNESS_SECONDS", "SCAN_WORKERS", "BOT_RESEARCH_REFERENCE_CAPITAL",
    ]
    for name in names:
        value = secret(name, "(default)")
        if any(token in name for token in ("TOKEN", "KEY", "SECRET")):
            value = "***CONFIGURED***" if value not in {"", "(default)"} else value
        st.write(f"**{name}:** {value}")
    st.warning("Live trading is hard-disabled by the application safety design and is not activated from this page.")


def backtest_page() -> None:
    header("📊 Backtesting", "The UI exposes persisted backtest records when present; it does not invent historical results.")
    for table in ("cycles", "signals", "trades"):
        df = records(table, 500)
        if not df.empty:
            st.subheader(table.capitalize())
            st.dataframe(df, use_container_width=True, hide_index=True)
    st.info("No dedicated persisted backtest result was found if the sections above are empty.")


def generic_data_page(page: str) -> None:
    # These pages are intentionally truthful rather than blank or fake.
    research_pages = {
        "Deep Research", "Sector Analysis", "Theme Analysis", "Value Migration",
        "Inflection Points", "Value Chain", "Profit Pool", "Fundamental Analysis",
        "Technical Analysis", "SCRAP Analysis", "Watchlist", "Basket Detail",
        "AI Baskets", "Stocks", "Paper Trading", "Live Trading",
    }
    if page in research_pages:
        research_placeholder(page)
    else:
        signals_page(page)


def main() -> None:
    mode_switch()
    page = st.sidebar.selectbox("Desk", PAGES)
    st.sidebar.markdown("---")
    st.sidebar.caption("Backend independent · 5-minute fallback · no price cap")

    if page == "Dashboard":
        dashboard()
    elif page == "New Chat":
        chat_page()
    elif page == "AI Prompt Guide":
        prompt_guide()
    elif page == "Stock Screener":
        screener()
    elif page in {"Stock Detail", "360° Stock Analysis"}:
        stock_detail(page)
    elif page == "Live Charts":
        live_charts()
    elif page in {"Portfolio", "Positions"}:
        positions_page()
    elif page == "Orders":
        orders_page()
    elif page in {"P&L", "Trade Journal", "Bot Performance"}:
        trades_page(page)
    elif page in {"System Health", "Diagnostics"}:
        events_page(page)
    elif page == "Settings":
        settings_page()
    elif page == "Backtesting":
        backtest_page()
    elif page == "News":
        news_page()
    elif page == "Rejected Signals":
        rejected_page()
    elif page in {"Trend Scanner", "Top Bullish", "Top Bearish", "Shift to Bullish", "Shift to Bearish"}:
        screener()
    else:
        generic_data_page(page)


main()
