from __future__ import annotations

import time
from typing import Any

import requests
import streamlit as st

from intraday_bot.config import settings

AUTH_BASE_URL = "https://auth.dhan.co"

st.set_page_config(page_title="Dhan OAuth Browser Login Diagnostic", layout="centered")
st.title("🧪 Dhan OAuth Browser Login Diagnostic")
st.info(
    "AUTH-ONLY test. This follows Dhan's API Key + API Secret OAuth flow. "
    "It generates a consent session, provides the Dhan browser-login URL, "
    "and can consume a tokenId after the browser login. No API credential, PIN, "
    "TOTP, tokenId, or Access Token is displayed or persisted."
)

client_id = (settings.dhan_client_id or "").strip()
api_key = (settings.dhan_api_key or "").strip()
api_secret = (settings._secret_or_env("DHAN_API_SECRET", "") if hasattr(settings, "_secret_or_env") else "")

# Read API secret directly through the same credential source rules without
# exposing it in the UI.
if not api_secret:
    import os
    api_secret = os.getenv("DHAN_API_SECRET", "")
    if not api_secret:
        try:
            secret_value = st.secrets.get("DHAN_API_SECRET", "")
            api_secret = "" if secret_value is None else str(secret_value)
        except Exception:
            api_secret = ""
api_secret = api_secret.strip()

st.write(f"Client ID configured: **{bool(client_id)}**")
st.write(f"API Key configured: **{bool(api_key)}**")
st.write(f"API Secret configured: **{bool(api_secret)}**")

if not (client_id and api_key and api_secret):
    st.error("Required Dhan OAuth settings are not available.")
    st.stop()

st.subheader("1. Generate OAuth consent")
st.caption("Run this once. A new consent session is created by Dhan.")

if "dhan_oauth_consent_app_id" not in st.session_state:
    st.session_state.dhan_oauth_consent_app_id = ""

if st.button("Generate Dhan OAuth consent", type="primary"):
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
            payload = {}

        st.write(f"Response time: **{elapsed_ms:.0f} ms**")
        safe = {
            "http_status": response.status_code,
            "status": payload.get("status") if isinstance(payload, dict) else None,
            "consentAppStatus": payload.get("consentAppStatus") if isinstance(payload, dict) else None,
            "consentAppId": "present" if isinstance(payload, dict) and payload.get("consentAppId") else "absent",
        }
        st.json(safe)

        if response.status_code == 200 and isinstance(payload, dict) and payload.get("consentAppId"):
            st.session_state.dhan_oauth_consent_app_id = str(payload["consentAppId"])
            st.success("✅ Dhan generated the OAuth consent session.")
        else:
            st.error("❌ Dhan did not generate an OAuth consent session.")
    except Exception as exc:
        st.error(f"❌ Generate-consent request failed: {type(exc).__name__}")

consent_app_id = st.session_state.dhan_oauth_consent_app_id
if consent_app_id:
    login_url = f"{AUTH_BASE_URL}/login/consentApp-login?consentAppId={consent_app_id}"
    st.subheader("2. Complete Dhan browser login")
    st.write("Open the Dhan login page below, complete the normal Dhan login/2FA, and let Dhan redirect you to the configured Redirect URL.")
    st.link_button("Open Dhan browser login", login_url)
    st.warning(
        "After successful login, Dhan redirects with a temporary tokenId. "
        "Enter that tokenId directly into this diagnostic below. Do not send the tokenId to chat."
    )

    st.subheader("3. Consume tokenId")
    token_id = st.text_input("tokenId from the Dhan redirect URL", type="password", placeholder="Paste tokenId here")

    if st.button("Consume tokenId and test OAuth authentication"):
        token_id = token_id.strip()
        if not token_id:
            st.error("Enter the tokenId from the successful Dhan redirect first.")
            st.stop()

        started = time.perf_counter()
        try:
            response = requests.get(
                f"{AUTH_BASE_URL}/app/consumeApp-consent",
                params={"tokenId": token_id},
                headers={"app_id": api_key, "app_secret": api_secret},
                timeout=15,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            try:
                payload = response.json()
            except ValueError:
                payload = {}

            st.write(f"Response time: **{elapsed_ms:.0f} ms**")
            safe: dict[str, Any] = {"http_status": response.status_code}
            if isinstance(payload, dict):
                for key in ("status", "dhanClientId", "expiryTime", "dhanClientName", "dhanClientUcc", "givenPowerOfAttorney"):
                    if key in payload:
                        safe[key] = "present" if key == "dhanClientId" else payload[key]
                safe["accessToken"] = "present" if payload.get("accessToken") else "absent"
                if payload.get("message"):
                    safe["message"] = payload["message"]
            st.json(safe)

            if isinstance(payload, dict) and payload.get("accessToken"):
                st.success("✅ Dhan OAuth authentication SUCCEEDED — an Access Token was generated.")
            else:
                st.error("❌ Dhan OAuth authentication did not return an Access Token.")
        except Exception as exc:
            st.error(f"❌ Consume-consent request failed: {type(exc).__name__}")

st.divider()
st.caption("Security: API credentials and temporary tokenId remain in memory only. No API key, API secret, PIN, TOTP, tokenId, or Access Token is displayed or persisted.")
