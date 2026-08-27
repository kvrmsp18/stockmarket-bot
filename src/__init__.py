"""Package bootstrap for the NSE/BSE intraday AI project.

When the dashboard runs on Streamlit Cloud, Streamlit secrets are the source
of truth for broker credentials. Load those values into the environment before
any src.* module constructs a DhanHQClient. Existing environment values are
intentionally overwritten by Streamlit secrets so a stale process environment
cannot keep an old Dhan access token after the secret is rotated.

This bootstrap is safe for GitHub Actions and local CLI runs: Streamlit is
optional at import time, and missing Streamlit secrets simply leave the normal
environment-based configuration untouched.
"""
from __future__ import annotations

import os


def _load_streamlit_secrets() -> None:
    try:
        import streamlit as st
    except Exception:
        return

    names = (
        "DHAN_CLIENT_ID",
        "DHAN_ACCESS_TOKEN",
        "DHAN_LIVE_TRADING_ENABLED",
        "DHAN_API_BASE_URL",
        "DHAN_AUTH_BASE_URL",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "BOT_RESEARCH_REFERENCE_CAPITAL",
        "DHAN_SECURITY_IDS_JSON",
        "BSE_SCRIP_CODES_JSON",
    )

    for name in names:
        try:
            value = st.secrets.get(name)
        except Exception:
            value = None
        if value is not None:
            os.environ[name] = str(value).strip()


_load_streamlit_secrets()
