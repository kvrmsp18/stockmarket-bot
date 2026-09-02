from __future__ import annotations

import time
from typing import Any

import requests
import streamlit as st

from intraday_bot.config import settings

AUTH_BASE_URL = "https://auth.dhan.co"

st.set_page_config(page_title="Dhan API Key Consent Diagnostic", layout="centered")
st.title("🧪 Dhan API Key + Secret Consent Diagnostic")
st.info(
    "AUTH-ONLY test. This checks whether the configured Dhan Client ID + API Key + API Secret "
    "are accepted by Dhan's Generate Consent endpoint. It does not generate an Access Token "
    "and never displays credentials."
)

client_id = (settings.dhan_client_id or "").strip()
api_key = (settings.dhan_api_key or "").strip()
api_secret = (settings.dhan_api_secret or "").strip()

st.write(f"Client ID configured: **{bool(client_id)}**")
st.write(f"API Key configured: **{bool(api_key)}**")
st.write(f"API Secret configured: **{bool(api_secret)}**")

if not (client_id and api_key and api_secret):
    st.error("Required Dhan API Key authentication settings are not available.")
    st.stop()

st.warning(
    "This performs one real Generate Consent request. Run it by itself; do not run other Dhan "
    "authentication diagnostics at the same time."
)

if st.button("Run Dhan API Key consent test", type="primary"):
    started = time.perf_counter()
    try:
        response = requests.post(
            f"{AUTH_BASE_URL}/app/generate-consent",
            params={"client_id": client_id},
            headers={"app_id": api_key, "app_secret": api_secret},
            timeout=15,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        try:
            payload: Any = response.json()
        except ValueError:
            payload = {"response_type": "non_json", "status_code": response.status_code}

        safe: dict[str, Any] = {"http_status": response.status_code}
        if isinstance(payload, dict):
            for key in ("status", "consentAppStatus", "message"):
                if key in payload:
                    safe[key] = payload[key]
            safe["consentAppId"] = "present" if payload.get("consentAppId") else "absent"
        else:
            safe["response_type"] = type(payload).__name__

        st.write(f"Response time: **{elapsed_ms:.0f} ms**")
        st.json(safe)

        success = isinstance(payload, dict) and str(payload.get("status", "")).lower() == "success" and bool(payload.get("consentAppId"))
        if success:
            st.success("✅ Dhan accepted the API Key + API Secret consent request.")
            st.info("This test only proves the API Key/Secret can start an OAuth consent session. It does not complete browser login or generate an Access Token.")
        else:
            st.error("❌ Dhan did not accept the API Key + API Secret consent request. The non-secret response above is the diagnostic evidence.")
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        st.write(f"Response time: **{elapsed_ms:.0f} ms**")
        st.error(f"❌ API Key consent test failed: {type(exc).__name__}")
        st.caption("No credentials are displayed or persisted.")

st.divider()
st.caption("Security: API credentials remain in memory only; no API key, API secret, consent ID, token, PIN, or OTP is displayed or persisted.")
