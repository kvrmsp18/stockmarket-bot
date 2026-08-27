"""Streamlit dashboard for the stockmarket bot.

The dashboard reads the persisted paper-trading journal and monitor health
snapshot written by GitHub Actions. It never places broker orders.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

st.set_page_config(
    page_title="Stockmarket Bot Dashboard",
    layout="wide",
    initial_sidebar_state="expanded",
)

ROOT = Path(__file__).parent.parent
STATUS_PATH = ROOT / "data" / "monitor_status.json"
IST = ZoneInfo("Asia/Kolkata")


@st.cache_resource
def load_validation_store():
    try:
        from src.validation_store import ValidationStore
        return ValidationStore("data/paper_trading_journal.jsonl")
    except Exception:
        return None


@st.cache_resource
def load_validation_tools():
    try:
        from src.validation_gate import evaluate, render_scorecard
        return evaluate, render_scorecard
    except Exception:
        return None, None


@st.cache_data(ttl=30)
def load_monitor_status() -> dict:
    if not STATUS_PATH.exists():
        return {"status": "NOT_RUN", "updated_at": None, "scan": {}, "actionable_candidates": []}
    try:
        payload = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {"status": "INVALID"}
    except Exception as exc:
        return {"status": "ERROR", "error": str(exc)}


def _format_time(value: str | None) -> str:
    if not value:
        return "Not available"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(IST).strftime("%d-%b-%Y %I:%M:%S %p IST")
    except (TypeError, ValueError):
        return str(value)


def _secret_or_env(name: str) -> str:
    """Read deployment secrets without ever displaying their full values."""
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""


def _mask(name: str, value: str) -> str:
    if not value:
        return "(not set)"
    if any(word in name for word in ("TOKEN", "SECRET", "ACCESS", "API_KEY")):
        if len(value) > 8:
            return value[:4] + "***" + value[-4:]
        return "***"
    return value


def page_overview():
    st.title("📊 Stockmarket Bot Dashboard")

    status = load_monitor_status()
    scan = status.get("scan") or {}
    candidates = status.get("actionable_candidates") or []
    bot_status = status.get("status", "NOT_RUN")

    if st.button("🔄 Refresh latest data"):
        load_monitor_status.clear()
        st.rerun()

    if bot_status == "OK":
        st.success(f"🟢 Bot heartbeat healthy — last cycle: {_format_time(status.get('updated_at'))}")
    elif bot_status == "FAILED":
        st.error(f"🔴 Last monitor cycle FAILED — {_format_time(status.get('updated_at'))}")
        st.code(str(status.get("error", "Unknown error")))
    elif bot_status == "NOT_RUN":
        st.warning("🟡 Monitor has not completed a recorded cycle yet.")
    else:
        st.warning(f"🟡 Monitor status: {bot_status}")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Frozen Signals", _journal_signal_count())
    with col2:
        st.metric("Resolved Outcomes", _journal_outcome_count())
    with col3:
        st.metric("Actionable Now", int(scan.get("actionable", 0)))
    with col4:
        st.metric("Stocks Scanned", int(scan.get("scanned", 0)))

    st.markdown("---")
    st.subheader("🤖 Monitor Health")
    health_col1, health_col2, health_col3 = st.columns(3)
    with health_col1:
        st.write(f"**Last cycle:** {_format_time(status.get('updated_at'))}")
        st.write(f"**Mode:** {status.get('mode', 'paper-trading')}")
        st.write(f"**Market window:** {status.get('market_window', '09:15–15:30 IST weekdays')}")
    with health_col2:
        funds = status.get("available_funds")
        funds_text = "Not available" if funds is None else f"₹{float(funds):,.2f}"
        st.write(f"**Available Dhan funds:** {funds_text}")
        st.write(f"**Funds source:** {status.get('funds_source', 'Not available')}")
        st.write(f"**Telegram heartbeat:** {'Configured' if status.get('telegram_configured') else 'NOT CONFIGURED'}")
    with health_col3:
        st.write(f"**BUY:** {int(scan.get('buy', 0))}")
        st.write(f"**SELL:** {int(scan.get('sell', 0))}")
        st.write(f"**New signals frozen:** {int(status.get('new_signals_frozen', 0))}")

    st.markdown("---")
    st.subheader("🎯 Current Suggested Stocks")
    if candidates:
        for candidate in candidates:
            direction = candidate.get("direction", "")
            title = f"{direction} {candidate.get('symbol', '')} — confidence {float(candidate.get('confidence', 0)):.0%}"
            with st.expander(title):
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.write(f"**Entry:** ₹{float(candidate['entry']):,.2f}")
                    st.write(f"**Stop Loss:** ₹{float(candidate['stop_loss']):,.2f}")
                    st.write(f"**Target:** ₹{float(candidate['target']):,.2f}")
                with c2:
                    st.write(f"**AI Quantity:** {int(candidate['quantity'])}")
                    st.write(f"**Capital Required:** ₹{float(candidate['capital_required']):,.2f}")
                    st.write(f"**Risk Amount:** ₹{float(candidate['risk_amount']):,.2f}")
                with c3:
                    st.write(f"**Confidence:** {float(candidate['confidence']):.1%}")
                    st.write(f"**Risk/Reward:** {float(candidate['risk_reward']):.2f}x")
                    st.write(f"**Risk:** {float(candidate['risk_percent']):.2f}%")
    else:
        st.info("No actionable stock passed all configured research, market-context, M/W/D, fundamental and risk gates in the latest cycle. This is different from the bot being stopped; the health panel above shows whether the scan actually ran.")

    st.markdown("---")
    st.subheader("🔎 Why candidates were filtered")
    rejection = status.get("rejection_breakdown") or {}
    if rejection:
        for reason, count in sorted(rejection.items(), key=lambda item: item[1], reverse=True):
            st.write(f"**{reason}:** {count}")
    else:
        st.caption("No rejection diagnostics recorded yet.")

    sources = status.get("data_sources") or {}
    if sources:
        st.subheader("📡 Market-data sources used")
        st.write(" · ".join(f"{name}: {count}" for name, count in sources.items()))

    st.markdown("---")
    st.subheader("📋 How It Works")
    st.write("""
    **GitHub Actions** runs the paper-trading monitor every 15 minutes during NSE market hours, from **9:15 AM to 3:30 PM IST on weekdays**.

    Each cycle:
    1. Fetches read-only intraday market data.
    2. Screens and ranks the managed stock universe.
    3. Applies market/sector, technical, M/W/D RSI, advanced, fundamental and risk gates.
    4. Freezes high-conviction paper signals only once per symbol per day.
    5. Sends a Telegram heartbeat with funds and suggested stocks when Telegram is configured.
    6. Persists monitor health, journal and reports back to GitHub.

    **Live trading remains disabled.** No real BUY/SELL orders are placed by this workflow.
    """)


def _journal_signal_count() -> int:
    store = load_validation_store()
    return len(store.signals()) if store else 0


def _journal_outcome_count() -> int:
    store = load_validation_store()
    return len(store.outcomes()) if store else 0


def page_signals():
    st.title("🎯 Frozen Signals & Outcomes")
    store = load_validation_store()
    if not store:
        st.warning("Journal is unavailable.")
        return

    try:
        signals = store.signals()
        outcomes = store.outcomes()
        outcome_by_signal = {rec["payload"]["signal_id"]: rec["payload"] for rec in outcomes}
        if not signals:
            st.info("No paper signals have been frozen yet.")
            return

        st.write(f"**Total signals:** {len(signals)} | **Resolved:** {len(outcomes)} | **Open:** {len(signals) - len(outcome_by_signal)}")
        st.markdown("---")
        for rec in signals[-20:][::-1]:
            sig = rec["payload"]
            outcome = outcome_by_signal.get(sig["signal_id"])
            status = "✓ Resolved" if outcome else "⏳ Open"
            with st.expander(f"{sig['symbol']} {sig['direction']} @ ₹{sig['entry']} — {status}"):
                c1, c2 = st.columns(2)
                with c1:
                    st.write(f"**Entry:** ₹{sig['entry']}")
                    st.write(f"**Stop Loss:** ₹{sig['stop_loss']}")
                    st.write(f"**Target:** ₹{sig['target']}")
                    st.write(f"**Quantity:** {sig['quantity']}")
                with c2:
                    st.write(f"**Generated:** {_format_time(sig['generated_at'])}")
                    st.write(f"**Confidence:** {sig['confidence']:.1%}")
                    st.write(f"**Risk/Reward:** {sig['risk_reward']:.2f}x")
                    if outcome:
                        st.write(f"**Outcome:** {outcome['outcome']}")
                        if outcome.get("net_pnl") is not None:
                            st.write(f"**Net P&L:** ₹{outcome['net_pnl']:.2f}")
    except Exception as exc:
        st.error(f"Error loading signals: {exc}")


def page_phase9():
    st.title("✅ Phase 9: Live-Trading Readiness Gate")
    st.write("The gate evaluates the locked paper-trading criteria before any decision to enable live trading.")
    st.markdown("---")

    store = load_validation_store()
    evaluate, render_scorecard = load_validation_tools()
    if not store or not evaluate:
        st.info("Readiness gate is not available yet.")
        return

    try:
        scorecard = evaluate(store, criteria_path="config/validation_criteria.json")
        st.markdown(render_scorecard(scorecard, decider="Dashboard User"))
        st.markdown("---")
        if scorecard.all_passed:
            st.success("All locked validation criteria passed. Live trading is still disabled until a deliberate manual decision is made.")
        else:
            st.warning(f"{len(scorecard.failed)} criterion/criteria not yet met. Continue paper validation.")
    except Exception as exc:
        st.warning(f"Cannot evaluate readiness yet: {exc}")


def page_settings():
    st.title("⚙️ Settings & Configuration")
    st.write("GitHub Actions and Streamlit Cloud are separate environments. GitHub Actions Secrets do **not** automatically appear in Streamlit Cloud.")

    st.info(
        "To configure this dashboard, open your Streamlit Cloud app's **Settings → Secrets** and paste the real values from your secure credential store. "
        "Do not commit real keys to GitHub. A safe template is available at `.streamlit/secrets.toml.example`."
    )

    env_vars = [
        ("DHAN_CLIENT_ID", "Dhan broker client ID"),
        ("DHAN_ACCESS_TOKEN", "Dhan access token"),
        ("DHAN_API_KEY", "Dhan API key"),
        ("DHAN_SECURITY_IDS_JSON", "Dhan instrument/security ID mapping"),
        ("BSE_SCRIP_CODES_JSON", "BSE scrip-code mapping"),
        ("TWELVEDATA_API_KEY", "Twelve Data intraday fallback key"),
        ("BOT_RESEARCH_REFERENCE_CAPITAL", "Fallback paper-trading reference capital"),
        ("TELEGRAM_BOT_TOKEN", "Telegram bot token"),
        ("TELEGRAM_CHAT_ID", "Telegram chat ID (optional when auto-discovery is enabled)"),
        ("TELEGRAM_AUTO_DISCOVER_CHAT_ID", "Allow safe unique Telegram chat-ID discovery"),
    ]
    for name, description in env_vars:
        value = _secret_or_env(name)
        marker = "✅" if value else "⚠️"
        st.write(f"{marker} **{name}** — {description}")
        st.caption(_mask(name, value))

    st.markdown("---")
    st.subheader("Workflow")
    st.write("**Schedule:** Every 15 minutes, 9:15 AM–3:30 PM IST, weekdays")
    st.write("**Mode:** Paper-trading only")
    st.write("**Live trading switch:** Hardcoded to `false` in the GitHub Actions workflow")
    st.write("**Health file:** `data/monitor_status.json`")
    st.write("**Journal:** `data/paper_trading_journal.jsonl`")
    st.write("**Criteria:** `config/validation_criteria.json`")


def main():
    st.sidebar.title("Navigation")
    page = st.sidebar.radio("Select page", ["Overview", "Signals & Outcomes", "Phase 9 Gate", "Settings"], index=0)

    if page == "Overview":
        page_overview()
    elif page == "Signals & Outcomes":
        page_signals()
    elif page == "Phase 9 Gate":
        page_phase9()
    else:
        page_settings()

    status = load_monitor_status()
    st.sidebar.markdown("---")
    st.sidebar.subheader("Status")
    st.sidebar.write(f"**Bot:** {status.get('status', 'NOT_RUN')}")
    st.sidebar.write("**Mode:** Paper-trading")
    st.sidebar.write("**Monitor:** Every 15 min (9:15–3:30 IST)")
    st.sidebar.write("**Last cycle:** " + _format_time(status.get("updated_at")))
    st.sidebar.write("**Repository:** github.com/kvrmsp18/stockmarket-bot")


if __name__ == "__main__":
    main()
