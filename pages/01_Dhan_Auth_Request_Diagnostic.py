from __future__ import annotations

import streamlit as st

from intraday_bot.config import settings

st.set_page_config(page_title="Dhan Authentication Diagnostic", layout="centered")

st.title("🧪 Dhan Authentication Diagnostic")
st.info(
    "This diagnostic is intentionally read-only. The bot does not generate, refresh, "
    "or persist Dhan Access Tokens using PIN/TOTP. The production monitor expects the "
    "manually entered access credential stored under DHAN_API_KEY."
)

st.subheader("Current credential configuration")
st.write(f"Client ID configured: **{'YES' if settings.dhan_client_id else 'NO'}**")
st.write(f"Manual credential configured: **{'YES' if settings.dhan_api_key else 'NO'}**")
st.write(f"Credential length: **{len(settings.dhan_api_key)}**")
st.write(f"Credential classification: **{settings.dhan_manual_credential_kind}**")

if settings.dhan_manual_credential_kind == "APP_KEY_NOT_ACCESS_TOKEN":
    st.error(
        "❌ The configured DHAN_API_KEY value is 8 characters and matches the shape of "
        "a Dhan application/API key. It is not a bearer Access Token, so the production "
        "broker will reject it rather than send it as the access-token header."
    )
elif settings.dhan_manual_credential_kind == "MANUAL_ACCESS_CREDENTIAL":
    st.success(
        "✅ A manually entered access credential is configured. The production broker "
        "will use it only as the Dhan access-token credential and will not generate a new token."
    )
else:
    st.error("❌ No manually entered Dhan access credential is configured.")

st.divider()
st.subheader("Automatic authentication")
st.write("PIN/TOTP token generation: **DISABLED**")
st.write("Automatic token refresh: **DISABLED**")
st.write("Access-token display/persistence: **DISABLED**")

st.warning(
    "This page no longer performs a live authentication-generation request. Use the "
    "production Dhan preflight in GitHub Actions to verify the configured credential "
    "against the real Dhan API."
)

st.caption("Security: no credential value, PIN, TOTP, OTP, or access-token value is displayed or persisted.")
