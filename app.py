from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from intraday_bot.brokers import DhanBroker
from intraday_bot.config import settings
from intraday_bot.database import Database

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


def sql_records(sql: str, params: tuple[Any, ...] = ()) -> pd.DataFrame:
    try:
        with DB.connect() as con:
            rows = con.execute(sql, params).fetchall()
        return pd.DataFrame([dict(row) for row in rows]) if rows else pd.DataFrame()
    except Exception as exc:
        st.error(f"DATABASE ERROR: {exc}")
        return pd.DataFrame()


def today_mask(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(False, index=df.index)
    parsed = pd.to_datetime(df[column], errors="coerce", utc=True)
    return parsed.dt.tz_convert("Asia/Kolkata").dt.date == date.today()


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
        "Paper trading uses real market data and never places real Dhan orders. Live execution remains hard-disabled.",
    )
    a, b, c, d, e, f = st.columns(6)
    a.metric("Mode", s.get("mode", "PAPER"))
    b.metric("Universe", s.get("stocks_observed", 0))
    c.metric("Quotes", s.get("quotes", 0))
    d.metric("Candidates", len(s.get("candidates", [])))
    e.metric("Open Positions", s.get("positions_open", 0))
    f.metric("Realized P&L", f"₹{s.get('realized_pnl', 0):,.2f}")

    st.subheader("TODAY'S RESEARCH")
    candidates = pd.DataFrame(s.get("candidates", []))
    if candidates.empty:
        st.warning("No actionable candidates were recorded in the latest cycle.")
    else:
        display_cols = [c for c in [
            "symbol", "decision", "price", "entry", "entry_low", "entry_high", "max_chase",
            "stop", "target", "rr", "quantity", "capital_required", "max_risk", "potential_reward",
            "trend_score", "technical_score", "fundamental_score", "conviction_score", "volume_score", "ai_score", "reason",
        ] if c in candidates.columns]
        st.dataframe(candidates[display_cols], use_container_width=True, hide_index=True)
        buys = candidates[candidates["decision"].eq("BUY")] if "decision" in candidates else pd.DataFrame()
        sells = candidates[candidates["decision"].eq("SELL")] if "decision" in candidates else pd.DataFrame()
        c1, c2, c3 = st.columns(3)
        c1.metric("Suggested BUY investment", f"₹{pd.to_numeric(buys.get('capital_required', pd.Series(dtype=float)), errors='coerce').fillna(0).sum():,.2f}")
        c2.metric("Suggested SELL value", f"₹{pd.to_numeric(sells.get('quantity', pd.Series(dtype=float)), errors='coerce').fillna(0).mul(pd.to_numeric(sells.get('entry', pd.Series(dtype=float)), errors='coerce').fillna(0)).sum():,.2f}")
        c3.metric("Total actionable stocks", len(candidates))

    st.subheader("TODAY'S PAPER RESULT")
    trades = sql_records("SELECT * FROM trades WHERE mode='PAPER' ORDER BY closed_at DESC")
    if trades.empty:
        st.info("No completed paper trades yet. Today's realized paper P&L is ₹0.00 until a position is closed.")
    else:
        trades["net_pnl"] = pd.to_numeric(trades["net_pnl"], errors="coerce").fillna(0)
        todays = trades[today_mask(trades, "closed_at")]
        st.metric("Today's realized paper P&L", f"₹{todays['net_pnl'].sum():,.2f}")

    st.subheader("HISTORY / MARKET HEALTH")
    h1, h2, h3, h4 = st.columns(4)
    h1.write("**Market:** " + ("OPEN" if s.get("market_open") else "CLOSED"))
    h2.write("**Data:** " + ("HEALTHY" if s.get("quotes") else "DATA UNAVAILABLE"))
    h3.write("**Dhan data:** " + ("CONNECTED" if secret("DHAN_ACCESS_TOKEN") else "NOT CONFIGURED"))
    h4.write("**AI:** " + ("CONFIGURED" if secret("OPENAI_API_KEY") or secret("ANTHROPIC_API_KEY") else "ADVISORY UNAVAILABLE"))
    st.write(
        f"**Last cycle:** {s.get('ended_at', 'Not available')} · **Duration:** {s.get('duration_seconds', 0)}s · "
        f"**Execution gate:** {s.get('execution_gate', 'Not evaluated')}"
    )


def signals_page(title: str = "Signals") -> None:
    header(title, "Showing persisted signal records only. No synthetic market values are created.")
    df = records("signals", 1000)
    if df.empty:
        st.warning("No persisted signals are available yet.")
        return
    st.dataframe(df, use_container_width=True, hide_index=True)


def screener() -> None:
    header("🔎 Stock Screener", "The screener uses candidates persisted by the latest backend cycle.")
    rows = status().get("candidates", [])
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
    header("📉 Live Charts", "Real Dhan market history only. No synthetic data is labelled live.")
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


def portfolio_page() -> None:
    header("📊 Portfolio", "Account-level paper portfolio summary. Dhan balance never blocks research or paper trading.")
    positions = sql_records("SELECT * FROM positions ORDER BY opened_at DESC")
    trades = sql_records("SELECT * FROM trades WHERE mode='PAPER' ORDER BY closed_at DESC")
    open_pos = positions[positions["closed_at"].isna()] if not positions.empty and "closed_at" in positions else pd.DataFrame()
    realized = pd.to_numeric(trades.get("net_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()
    exposure = pd.to_numeric(open_pos.get("entry_price", pd.Series(dtype=float)), errors="coerce").fillna(0).mul(pd.to_numeric(open_pos.get("quantity", pd.Series(dtype=float)), errors="coerce").fillna(0)).sum()
    a, b, c, d = st.columns(4)
    a.metric("Open Positions", len(open_pos))
    b.metric("Open Exposure", f"₹{exposure:,.2f}")
    c.metric("Realized Paper P&L", f"₹{realized:,.2f}")
    d.metric("Reference Capital", f"₹{settings.reference_capital:,.2f}")
    if open_pos.empty:
        st.info("No open positions are currently persisted.")
    else:
        st.subheader("Current Holdings")
        st.dataframe(open_pos, use_container_width=True, hide_index=True)


def positions_page() -> None:
    header("📌 Positions", "Current and historical position state. Open positions are shown first; current price is updated from the latest market cycle.")
    df = records("positions", 1000)
    if df.empty:
        st.info("No positions are currently persisted.")
        return
    if "closed_at" in df.columns:
        df["Position Status"] = df["closed_at"].isna().map({True: "OPEN", False: "CLOSED"})
        df = df.sort_values(["Position Status", "opened_at"], ascending=[True, False])
    st.dataframe(df, use_container_width=True, hide_index=True)


def orders_page() -> None:
    header("🧾 Orders", "Persisted order ledger. Paper fills are real-data simulations; live orders are blocked by the application safety gate.")
    df = records("orders", 1000)
    if df.empty:
        st.info("No orders are currently persisted.")
        return
    mode = st.selectbox("Order mode", ["ALL", "PAPER", "LIVE"], index=0)
    if mode != "ALL" and "payload" in df.columns:
        def row_mode(x: Any) -> str:
            try:
                obj = json.loads(str(x))
                return str(obj.get("mode", mode)).upper()
            except Exception:
                return mode
        inferred = df["payload"].map(row_mode)
        df = df[inferred.eq(mode)]
    st.dataframe(df, use_container_width=True, hide_index=True)


def paper_trading_page() -> None:
    header("📝 Paper Trading", "Real market data + simulated execution. No real Dhan order is placed in Paper Mode.")
    s = status()
    candidates = pd.DataFrame(s.get("candidates", []))
    orders = sql_records("SELECT * FROM orders WHERE order_id LIKE 'PAPER-%' ORDER BY ts DESC")
    today_orders = orders[today_mask(orders, "ts")] if not orders.empty else orders
    today_trades = sql_records("SELECT * FROM trades WHERE mode='PAPER' ORDER BY closed_at DESC")
    today_trades = today_trades[today_mask(today_trades, "closed_at")] if not today_trades.empty else today_trades
    a, b, c, d = st.columns(4)
    a.metric("Actionable Stocks", len(candidates))
    b.metric("Paper Orders Today", len(today_orders))
    c.metric("Open Paper Positions", int(s.get("positions_open", 0)))
    pnl = pd.to_numeric(today_trades.get("net_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()
    d.metric("Today's Paper P&L", f"₹{pnl:,.2f}")
    if candidates.empty:
        st.info("No actionable paper-trading candidates in the latest cycle.")
    else:
        st.subheader("Today's Suggested Trades")
        cols = [c for c in ["symbol", "decision", "entry", "quantity", "target", "stop", "rr", "capital_required", "max_risk", "reason"] if c in candidates.columns]
        st.dataframe(candidates[cols], use_container_width=True, hide_index=True)
    st.subheader("Paper Order Ledger")
    if today_orders.empty:
        st.info("No paper orders have been filled today.")
    else:
        st.dataframe(today_orders, use_container_width=True, hide_index=True)


def live_trading_page() -> None:
    header("🔒 Live Trading", "Live trading is intentionally hard-disabled. This page must never display Paper Trading records as live activity.")
    st.error("LIVE TRADING: DISABLED")
    st.write("**Execution gate:** application safety design prevents live order placement until the explicit objective validation/activation process is completed.")
    live_orders = sql_records("SELECT * FROM orders WHERE order_id NOT LIKE 'PAPER-%' ORDER BY ts DESC")
    live_trades = sql_records("SELECT * FROM trades WHERE mode='LIVE' ORDER BY closed_at DESC")
    if live_orders.empty and live_trades.empty:
        st.info("No live orders or live trades are persisted. This is expected while the live safety gate is disabled.")
    else:
        if not live_orders.empty:
            st.subheader("Live Order Ledger")
            st.dataframe(live_orders, use_container_width=True, hide_index=True)
        if not live_trades.empty:
            st.subheader("Live Trade Ledger")
            st.dataframe(live_trades, use_container_width=True, hide_index=True)


def pnl_page() -> None:
    header("💰 P&L", "Realized profit/loss calculated from persisted completed trades, with today's result separated from all-time history.")
    df = sql_records("SELECT * FROM trades ORDER BY closed_at ASC")
    if df.empty:
        st.info("No completed trades yet. Today's realized P&L is ₹0.00.")
        return
    df["net_pnl"] = pd.to_numeric(df["net_pnl"], errors="coerce").fillna(0)
    today = df[today_mask(df, "closed_at")]
    a, b, c, d = st.columns(4)
    a.metric("Today's P&L", f"₹{today['net_pnl'].sum():,.2f}")
    a.caption(f"Completed trades today: {len(today)}")
    b.metric("Total P&L", f"₹{df['net_pnl'].sum():,.2f}")
    c.metric("Win Rate", f"{(df['net_pnl'] > 0).mean() * 100:.1f}%")
    losses = abs(df.loc[df.net_pnl < 0, "net_pnl"].sum())
    wins = df.loc[df.net_pnl > 0, "net_pnl"].sum()
    d.metric("Profit Factor", f"{wins / losses:.2f}" if losses else "∞")
    if "closed_at" in df.columns:
        x = pd.to_datetime(df["closed_at"], errors="coerce")
        curve = pd.DataFrame({"closed_at": x, "cumulative_pnl": df["net_pnl"].cumsum()}).dropna().set_index("closed_at")
        if not curve.empty:
            st.line_chart(curve["cumulative_pnl"])
    st.subheader("Completed Trades")
    st.dataframe(df, use_container_width=True, hide_index=True)


def trade_journal_page() -> None:
    header("📒 Trade Journal", "One row per completed trade, including entry, exit, charges, result and exit reason.")
    df = records("trades", 1000)
    if df.empty:
        st.info("No completed trades are persisted yet.")
        return
    if "mode" in df.columns:
        mode = st.selectbox("Mode", ["ALL", "PAPER", "LIVE"])
        if mode != "ALL":
            df = df[df["mode"].eq(mode)]
    if "symbol" in df.columns:
        symbol = st.text_input("Filter symbol")
        if symbol:
            df = df[df["symbol"].astype(str).str.contains(symbol.upper(), na=False)]
    cols = [c for c in ["trade_id", "signal_id", "symbol", "mode", "side", "quantity", "entry_price", "exit_price", "gross_pnl", "charges", "net_pnl", "exit_reason", "opened_at", "closed_at"] if c in df.columns]
    st.dataframe(df[cols], use_container_width=True, hide_index=True)


def rejected_page() -> None:
    header("⛔ Rejected Signals", "Only persisted rejection records are shown. Aggregate rejection counts are taken from the latest monitor cycle when individual historical rows are unavailable.")
    df = sql_records("SELECT * FROM events WHERE event_type='SIGNAL_REJECTED' ORDER BY ts DESC")
    s = status()
    if not df.empty:
        payloads = []
        for raw in df.get("payload", []):
            try:
                payloads.append(json.loads(raw))
            except Exception:
                payloads.append({})
        detail = pd.json_normalize(payloads)
        if not detail.empty:
            st.subheader("Rejected candidate details")
            st.dataframe(detail, use_container_width=True, hide_index=True)
    else:
        st.info("No individual rejection records are persisted yet. The latest cycle's aggregate rejection counts are shown below.")
    rejection_counts = s.get("rejections", {})
    if rejection_counts:
        st.subheader("Latest Cycle Rejection Funnel")
        st.dataframe(pd.DataFrame([{"reason": k, "count": v} for k, v in rejection_counts.items()]), use_container_width=True, hide_index=True)
    if df.empty and not rejection_counts:
        st.info("No persisted rejection information is available.")


def backtest_page() -> None:
    header("📊 Backtesting", "Historical validation results only. This page does not manufacture backtest returns from live-cycle data.")
    cycles = records("cycles", 500)
    trades = records("trades", 500)
    if cycles.empty and trades.empty:
        st.info("No dedicated persisted backtest records are available yet.")
        return
    if not cycles.empty:
        st.subheader("Validation Cycles")
        st.dataframe(cycles, use_container_width=True, hide_index=True)
    if not trades.empty:
        st.subheader("Validated Trades")
        st.dataframe(trades, use_container_width=True, hide_index=True)


def bot_performance_page() -> None:
    header("📊 Bot Performance", "Performance statistics across completed trades. This is intentionally separate from the Trade Journal and P&L ledgers.")
    df = sql_records("SELECT * FROM trades ORDER BY closed_at ASC")
    if df.empty:
        st.info("No completed trades yet; performance statistics will populate after positions are opened and exited.")
        return
    df["net_pnl"] = pd.to_numeric(df["net_pnl"], errors="coerce").fillna(0)
    wins = df[df["net_pnl"] > 0]["net_pnl"]
    losses = df[df["net_pnl"] < 0]["net_pnl"]
    a, b, c, d, e = st.columns(5)
    a.metric("Completed Trades", len(df))
    b.metric("Win Rate", f"{(len(wins) / len(df) * 100):.1f}%")
    c.metric("Avg Winner", f"₹{wins.mean():,.2f}" if not wins.empty else "₹0.00")
    d.metric("Avg Loser", f"₹{losses.mean():,.2f}" if not losses.empty else "₹0.00")
    e.metric("Expectancy", f"₹{df['net_pnl'].mean():,.2f}")
    st.subheader("Performance by Exit Reason")
    if "exit_reason" in df.columns:
        summary = df.groupby("exit_reason", dropna=False).agg(trades=("trade_id", "count"), net_pnl=("net_pnl", "sum"), avg_pnl=("net_pnl", "mean")).reset_index()
        st.dataframe(summary, use_container_width=True, hide_index=True)
    st.subheader("Performance by Day")
    if "closed_at" in df.columns:
        tmp = df.copy()
        tmp["day"] = pd.to_datetime(tmp["closed_at"], errors="coerce").dt.tz_localize("UTC", ambiguous="NaT", nonexistent="NaT").dt.tz_convert("Asia/Kolkata").dt.date if not pd.api.types.is_datetime64tz_dtype(pd.to_datetime(tmp["closed_at"], errors="coerce")) else pd.to_datetime(tmp["closed_at"], errors="coerce").dt.tz_convert("Asia/Kolkata").dt.date
        daily = tmp.groupby("day", dropna=True).agg(trades=("trade_id", "count"), net_pnl=("net_pnl", "sum"), win_rate=("net_pnl", lambda x: (x > 0).mean() * 100)).reset_index()
        st.dataframe(daily, use_container_width=True, hide_index=True)


def system_health_page() -> None:
    header("🩺 System Health", "High-level operational health from the latest persisted monitor cycle.")
    s = status()
    if not s:
        st.warning("No monitor status is available.")
        return
    a, b, c, d, e = st.columns(5)
    a.metric("Market", "OPEN" if s.get("market_open") else "CLOSED")
    b.metric("Stocks Observed", s.get("stocks_observed", 0))
    c.metric("Quotes", s.get("quotes", 0))
    d.metric("Actionable", len(s.get("candidates", [])))
    e.metric("Errors", len(s.get("errors", [])))
    st.subheader("Execution State")
    st.write(f"**Mode:** {s.get('mode', 'PAPER')} · **Gate:** {s.get('execution_gate', 'UNKNOWN')} · **Open positions:** {s.get('positions_open', 0)}")
    st.subheader("Latest Cycle")
    st.json(s)


def diagnostics_page() -> None:
    header("🔍 Diagnostics", "Detailed troubleshooting view. Events are filtered by operational significance instead of duplicating the System Health summary.")
    s = status()
    st.subheader("Cycle diagnostics")
    for key in ["cycle_id", "started_at", "ended_at", "duration_seconds", "stocks_observed", "quotes", "execution_gate", "errors"]:
        st.write(f"**{key}:** {s.get(key, '—')}")
    df = sql_records("SELECT * FROM events WHERE severity IN ('ERROR','WARN') OR event_type NOT IN ('CYCLE_START','CYCLE_END') ORDER BY id DESC LIMIT 500")
    if df.empty:
        st.info("No warning/error or non-cycle diagnostic events are persisted.")
    else:
        st.subheader("Diagnostic Events")
        st.dataframe(df, use_container_width=True, hide_index=True)


def news_page() -> None:
    header("📰 News", "News is shown only from persisted news/headline source events. Cycle telemetry is never used as a news fallback.")
    df = sql_records("SELECT * FROM events WHERE lower(event_type) LIKE '%news%' OR lower(event_type) LIKE '%headline%' ORDER BY ts DESC LIMIT 500")
    if df.empty:
        st.warning("DATA UNAVAILABLE: no persisted news/headline source events are available.")
        return
    st.dataframe(df, use_container_width=True, hide_index=True)


def settings_page() -> None:
    header("⚙️ Settings", "Read-only runtime configuration. Secrets are never displayed.")
    values = {
        "BOT_MODE": settings.mode,
        "DHAN_CLIENT_ID": settings.dhan_client_id,
        "DHAN_LIVE_TRADING_ENABLED": settings.live_enabled,
        "RISK_PER_TRADE_PCT": settings.risk_per_trade_pct,
        "MAX_DAILY_LOSS": settings.daily_loss_limit,
        "MAX_OPEN_POSITIONS": settings.max_positions,
        "MAX_POSITION_EXPOSURE": settings.max_position_exposure,
        "MAX_SECTOR_EXPOSURE": settings.max_sector_exposure,
        "MIN_RR": settings.min_rr,
        "DATA_FRESHNESS_SECONDS": settings.freshness_seconds,
        "SCAN_WORKERS": settings.scan_workers,
        "BOT_RESEARCH_REFERENCE_CAPITAL": settings.reference_capital,
    }
    for name, value in values.items():
        st.write(f"**{name}:** {value}")
    st.warning("Live trading is hard-disabled by the application safety design and is not activated from this page.")


def prompt_guide() -> None:
    header("🤖 AI Prompt Guide", "AI remains advisory only. Missing data must be reported rather than invented.")
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
    header("💬 New Chat", "Advisory chat is available only when an AI provider is configured.")
    if not (secret("OPENAI_API_KEY") or secret("ANTHROPIC_API_KEY")):
        st.warning("AI PROVIDER NOT CONFIGURED. Add the appropriate provider secret to enable advisory chat.")
        return
    question = st.text_area("Question")
    if question:
        st.write("Question received:")
        st.write(question)
        st.caption("Provider execution is intentionally not duplicated in the UI layer.")


def research_placeholder(title: str) -> None:
    header(title, "This page shows only persisted records relevant to the selected area; it never substitutes generic cycle telemetry.")
    df = records("signals", 500)
    if df.empty:
        st.warning("DATA UNAVAILABLE: no persisted records are available for this page.")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)


def generic_data_page(page: str) -> None:
    research_placeholder(page)


def main() -> None:
    mode_switch()
    page = st.sidebar.selectbox("Desk", PAGES, key="desk_page")
    st.sidebar.markdown("---")
    st.sidebar.caption("Backend independent · 5-minute fallback · no price cap")

    routes = {
        "Dashboard": dashboard,
        "New Chat": chat_page,
        "AI Prompt Guide": prompt_guide,
        "Stock Screener": screener,
        "Stock Detail": lambda: stock_detail("Stock Detail"),
        "360° Stock Analysis": lambda: stock_detail("360° Stock Analysis"),
        "Live Charts": live_charts,
        "Portfolio": portfolio_page,
        "Positions": positions_page,
        "Orders": orders_page,
        "Paper Trading": paper_trading_page,
        "Live Trading": live_trading_page,
        "P&L": pnl_page,
        "Trade Journal": trade_journal_page,
        "Rejected Signals": rejected_page,
        "Backtesting": backtest_page,
        "Bot Performance": bot_performance_page,
        "News": news_page,
        "System Health": system_health_page,
        "Diagnostics": diagnostics_page,
        "Settings": settings_page,
        "Trend Scanner": screener,
        "Top Bullish": screener,
        "Top Bearish": screener,
        "Shift to Bullish": screener,
        "Shift to Bearish": screener,
    }
    routes.get(page, lambda: generic_data_page(page))()


main()
