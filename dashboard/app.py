"""Streamlit dashboard for the stockmarket bot.

Reads from data/paper_trading_journal.jsonl to show live monitor status,
frozen signals, outcomes, daily/weekly summaries, and the Phase 9 readiness gate.

Streamlit secrets (Settings > Secrets in Streamlit Cloud) are automatically
loaded into the environment by __init__.py, so all src modules see them as
environment variables without modification.
"""

import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

from src.stock_monitor import StockMonitorSnapshot
from src.validation_gate import evaluate, render_scorecard
from src.validation_store import ValidationStore

# ---- Configuration ----
JOURNAL_PATH = "data/paper_trading_journal.jsonl"
CRITERIA_PATH = "config/validation_criteria.json"

st.set_page_config(
    page_title="Stockmarket Bot",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _journal() -> ValidationStore:
    return ValidationStore(JOURNAL_PATH)


def _load_signals_and_outcomes():
    """Load all frozen signals and their outcomes."""
    store = _journal()
    signals_by_id = {
        rec["payload"]["signal_id"]: rec["payload"]
        for rec in store.signals()
    }
    outcomes_by_signal = {
        rec["payload"]["signal_id"]: rec["payload"]
        for rec in store.outcomes()
    }
    return signals_by_id, outcomes_by_signal


def _format_datetime(dt_str):
    """Parse ISO datetime and format nicely."""
    try:
        dt = datetime.fromisoformat(dt_str)
        return dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    except:
        return dt_str


# ---- Page: Overview ----
def page_overview():
    st.title("📊 Stockmarket Bot Dashboard")
    
    col1, col2, col3 = st.columns(3)
    
    store = _journal()
    signals = store.signals()
    outcomes = store.outcomes()
    
    with col1:
        st.metric("Frozen Signals", len(signals))
    with col2:
        st.metric("Resolved Outcomes", len(outcomes))
    with col3:
        open_count = len(signals) - len(set(rec["payload"]["signal_id"] for rec in outcomes))
        st.metric("Still Open", open_count)
    
    st.markdown("---")
    
    if signals:
        st.subheader("Latest 5 Frozen Signals")
        for rec in signals[-5:][::-1]:
            sig = rec["payload"]
            status = "✓ Resolved" if sig["signal_id"] in {o["payload"]["signal_id"] for o in outcomes} else "⏳ Open"
            st.write(f"**{sig['symbol']}** {sig['direction']} @ {sig['entry']} | {status}")
    else:
        st.info("No signals frozen yet.")


# ---- Page: Signals & Outcomes ----
def page_signals():
    st.title("🎯 Frozen Signals & Outcomes")
    
    signals_by_id, outcomes_by_signal = _load_signals_and_outcomes()
    
    if not signals_by_id:
        st.info("No signals frozen yet.")
        return
    
    for sig_id, sig in list(signals_by_id.items())[-10:][::-1]:
        with st.expander(f"{sig['symbol']} {sig['direction']} @ {sig['entry']} — {sig_id[:16]}..."):
            col1, col2 = st.columns(2)
            with col1:
                st.write(f"**Entry:** {sig['entry']}")
                st.write(f"**Stop Loss:** {sig['stop_loss']}")
                st.write(f"**Target:** {sig['target']}")
                st.write(f"**Quantity:** {sig['quantity']}")
            with col2:
                st.write(f"**Generated:** {_format_datetime(sig['generated_at'])}")
                st.write(f"**Confidence:** {sig['confidence']:.0%}")
                st.write(f"**Risk/Reward:** {sig['risk_reward']:.2f}")
            
            if sig_id in outcomes_by_signal:
                outcome = outcomes_by_signal[sig_id]
                st.write(f"**Outcome:** {outcome['outcome']}")
                if outcome.get("exit_price"):
                    st.write(f"**Exit Price:** {outcome['exit_price']}")
                    st.write(f"**Net P&L:** {outcome['net_pnl']:.2f}")


# ---- Page: Phase 9 Readiness Gate ----
def page_phase9():
    st.title("✅ Phase 9: Live-Trading Readiness Gate")
    
    if not Path(CRITERIA_PATH).exists():
        st.error(f"Criteria file not found at {CRITERIA_PATH}")
        st.info("Copy `validation_criteria.json` to the repo first.")
        return
    
    if not Path(JOURNAL_PATH).exists():
        st.warning("No journal yet. Run the monitor first.")
        return
    
    try:
        store = _journal()
        scorecard = evaluate(store, criteria_path=CRITERIA_PATH)
        
        st.markdown(render_scorecard(scorecard, decider="Dashboard User"))
        
        st.markdown("---")
        st.subheader("Next Steps")
        if scorecard.all_passed:
            st.success("✓ All criteria passed. Review the scorecard above, then edit `.github/workflows/continuous-monitor.yml` to flip `DHAN_LIVE_TRADING_ENABLED` to `true` and push to GitHub.")
        else:
            st.warning(f"⚠️ {len(scorecard.failed)} criterion/criteria did not pass. Keep monitoring.")
    
    except Exception as e:
        st.error(f"Error evaluating readiness: {e}")


# ---- Page: Settings ----
def page_settings():
    st.title("⚙️ Settings & Configuration")
    
    st.subheader("Environment Variables")
    st.write("These are loaded from `.env` (local) or Streamlit Secrets (cloud):")
    
    env_vars = [
        ("DHAN_CLIENT_ID", "Dhan broker client ID"),
        ("DHAN_ACCESS_TOKEN", "Dhan broker access token"),
        ("DHAN_SECURITY_IDS_JSON", "Instrument mapping (JSON)"),
        ("BOT_RESEARCH_REFERENCE_CAPITAL", "Reference equity for position sizing"),
        ("TELEGRAM_BOT_TOKEN", "Telegram bot token (optional)"),
        ("TELEGRAM_CHAT_ID", "Telegram chat ID (optional)"),
        ("DHAN_LIVE_TRADING_ENABLED", "Set to 'true' only after Phase 9 passes"),
    ]
    
    for var, desc in env_vars:
        value = os.getenv(var, "(not set)")
        # Mask sensitive values
        if "TOKEN" in var or "SECRET" in var or "ACCESS" in var:
            if len(value) > 10:
                value = value[:4] + "***" + value[-4:]
        st.write(f"**{var}**: {value}")
    
    st.markdown("---")
    st.subheader("Files & Paths")
    st.write(f"**Journal:** `{JOURNAL_PATH}`")
    st.write(f"**Criteria:** `{CRITERIA_PATH}`")
    st.write(f"**Reports:** `reports/` (daily/ and weekly/ subdirs)")


# ---- Main App ----
def main():
    st.sidebar.title("Navigation")
    page = st.sidebar.radio(
        "Select page",
        ["Overview", "Signals & Outcomes", "Phase 9 Gate", "Settings"],
        index=0,
    )
    
    if page == "Overview":
        page_overview()
    elif page == "Signals & Outcomes":
        page_signals()
    elif page == "Phase 9 Gate":
        page_phase9()
    elif page == "Settings":
        page_settings()
    
    st.sidebar.markdown("---")
    st.sidebar.write("**Status:** Paper-trading mode")
    st.sidebar.write("**Live Orders:** Disabled until Phase 9 passes")


if __name__ == "__main__":
    main()
