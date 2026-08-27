"""Streamlit dashboard for the stockmarket bot.

Reads from data/paper_trading_journal.jsonl to show live monitor status,
frozen signals, outcomes, daily/weekly summaries, and the Phase 9 readiness gate.
"""

import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import streamlit as st

# Fix import path for Streamlit Cloud
sys.path.insert(0, str(Path(__file__).parent.parent))

# Configuration
st.set_page_config(
    page_title="Stockmarket Bot",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Cache validation store and tools
@st.cache_resource
def load_validation_store():
    """Load the paper-trading journal store."""
    try:
        from src.validation_store import ValidationStore
        return ValidationStore("data/paper_trading_journal.jsonl")
    except Exception as e:
        return None


@st.cache_resource
def load_validation_tools():
    """Load the validation gate evaluation tools."""
    try:
        from src.validation_gate import evaluate, render_scorecard
        return evaluate, render_scorecard
    except Exception as e:
        return None, None


# ---- Page: Overview ----
def page_overview():
    """Dashboard overview with signal counts and status."""
    st.title("📊 Stockmarket Bot Dashboard")

    col1, col2, col3 = st.columns(3)

    store = load_validation_store()
    if store:
        try:
            signals = store.signals()
            outcomes = store.outcomes()
            outcome_ids = {rec["payload"]["signal_id"] for rec in outcomes}
            open_count = len(signals) - len(outcome_ids)

            with col1:
                st.metric("Frozen Signals", len(signals))
            with col2:
                st.metric("Resolved Outcomes", len(outcomes))
            with col3:
                st.metric("Still Open", open_count)
        except Exception as e:
            st.error(f"Error loading data: {e}")
    else:
        with col1:
            st.metric("Status", "Initializing", "Waiting for first cycle")
        with col2:
            st.write("")
        with col3:
            st.write("")

    st.markdown("---")

    st.subheader("📋 How It Works")
    st.write("""
    **GitHub Actions** runs the monitor every 15 minutes during market hours (9:15 AM - 3:30 PM IST).
    
    Each cycle:
    1. Analyzes market data from Dhan
    2. Screens and ranks stocks
    3. Freezes paper signals for high-conviction candidates
    4. Simulates trades (paper mode, no real orders)
    5. Commits results to the journal
    
    Data updates here automatically as cycles complete. Refresh to see the latest results.
    """)

    st.markdown("---")

    st.info("💡 Tip: Check back in ~15 minutes to see data from the next monitor cycle (9:15–3:30 IST weekdays).")


# ---- Page: Signals & Outcomes ----
def page_signals():
    """View frozen signals and their outcomes."""
    st.title("🎯 Frozen Signals & Outcomes")

    store = load_validation_store()
    if not store:
        st.warning("Journal not available yet. Dashboard initializing...")
        return

    try:
        signals = store.signals()
        outcomes = store.outcomes()
        outcome_by_signal = {
            rec["payload"]["signal_id"]: rec["payload"] for rec in outcomes
        }

        if not signals:
            st.info("No signals frozen yet. Waiting for the next monitor cycle.")
            return

        st.write(f"**Total signals:** {len(signals)} | **Resolved:** {len(outcomes)}")
        st.markdown("---")

        for rec in signals[-10:][::-1]:
            sig = rec["payload"]
            outcome = outcome_by_signal.get(sig["signal_id"])
            status = "✓ Resolved" if outcome else "⏳ Open"

            with st.expander(
                f"{sig['symbol']} {sig['direction']} @ {sig['entry']} — {status}"
            ):
                col1, col2 = st.columns(2)

                with col1:
                    st.write(f"**Symbol:** {sig['symbol']}")
                    st.write(f"**Direction:** {sig['direction']}")
                    st.write(f"**Entry:** {sig['entry']}")
                    st.write(f"**Stop Loss:** {sig['stop_loss']}")
                    st.write(f"**Target:** {sig['target']}")
                    st.write(f"**Quantity:** {sig['quantity']}")

                with col2:
                    st.write(f"**Generated:** {sig['generated_at']}")
                    st.write(f"**Confidence:** {sig['confidence']:.1%}")
                    st.write(f"**Risk/Reward:** {sig['risk_reward']:.2f}x")
                    if outcome:
                        st.write(f"**Outcome:** {outcome['outcome']}")
                        if outcome.get("net_pnl"):
                            st.write(f"**Net P&L:** ₹{outcome['net_pnl']:.2f}")

    except Exception as e:
        st.error(f"Error loading signals: {e}")


# ---- Page: Phase 9 Gate ----
def page_phase9():
    """Live-trading readiness validation gate."""
    st.title("✅ Phase 9: Live-Trading Readiness Gate")

    st.write("""
    This gate evaluates whether the paper-trading record meets objective criteria for live trading.
    
    **Locked criteria** prevent moving the goalposts after results are in. Criteria are set before
    the validation window starts and cannot be changed retroactively.
    """)

    st.markdown("---")

    store = load_validation_store()
    evaluate, render_scorecard = load_validation_tools()

    if not store or not evaluate:
        st.info("Readiness gate will appear here after the first monitor cycle.")
        st.write("Check back in 15-30 minutes for initial data.")
        return

    try:
        scorecard = evaluate(store, criteria_path="config/validation_criteria.json")

        st.markdown(render_scorecard(scorecard, decider="Dashboard User"))

        st.markdown("---")

        if scorecard.all_passed:
            st.success(
                "✅ All criteria passed! \n\n"
                "Next steps: \n"
                "1. Review the scorecard above carefully \n"
                "2. Make an informed decision before enabling live trading \n"
                "3. Edit `.github/workflows/continuous-monitor.yml` to set `DHAN_LIVE_TRADING_ENABLED = true` "
                "(if desired after manual review)"
            )
        else:
            st.warning(
                f"⏳ {len(scorecard.failed)} criterion/criteria not yet met. Keep monitoring."
            )

    except Exception as e:
        st.warning(f"Cannot evaluate readiness yet: {e}")


# ---- Page: Settings ----
def page_settings():
    """View environment configuration."""
    st.title("⚙️ Settings & Configuration")

    st.subheader("Environment Variables")
    st.write(
        "These are loaded from Streamlit Secrets (Secrets tab in 'Manage app') "
        "or your local `.env` file."
    )

    env_vars = [
        ("DHAN_CLIENT_ID", "Dhan broker client ID"),
        ("DHAN_ACCESS_TOKEN", "Dhan broker access token"),
        ("DHAN_SECURITY_IDS_JSON", "Instrument ID mapping (JSON)"),
        ("BSE_SCRIP_CODES_JSON", "BSE codes (JSON)"),
        ("BOT_RESEARCH_REFERENCE_CAPITAL", "Reference capital for position sizing"),
        ("TELEGR AM_BOT_TOKEN", "Telegram bot token (optional)"),
        ("TELEGRAM_CHAT_ID", "Telegram chat ID (optional)"),
        ("DHAN_LIVE_TRADING_ENABLED", "Live trading switch (hardcoded in workflow)"),
    ]

    for var, desc in env_vars:
        value = os.getenv(var, "(not set)")

        # Mask sensitive values
        if any(word in var for word in ["TOKEN", "SECRET", "ACCESS"]):
            if len(value) > 10 and value != "(not set)":
                value = value[:4] + "***" + value[-4:]

        status = "✅" if value != "(not set)" else "⚠️"
        st.write(f"{status} **{var}** — {desc}")
        st.caption(value)

    st.markdown("---")

    st.subheader("Files & Directories")
    st.write("""
    - **Journal:** `data/paper_trading_journal.jsonl` (persisted on GitHub)
    - **Criteria:** `config/validation_criteria.json` (locked pass/fail rules)
    - **Reports:** `reports/daily/` and `reports/weekly/` (validation summaries)
    - **Workflow:** `.github/workflows/continuous-monitor.yml` (scheduler config)
    """)


# ---- Main App ----
def main():
    """Main dashboard app with navigation."""
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
    st.sidebar.subheader("Status")
    st.sidebar.write("**Mode:** Paper-trading")
    st.sidebar.write("**Monitor:** Every 15 min (9:15–3:30 IST)")
    st.sidebar.write("**Repository:** github.com/kvrmsp18/stockmarket-bot")


if __name__ == "__main__":
    main()