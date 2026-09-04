from __future__ import annotations

import streamlit as st

from intraday_bot.config import settings

st.set_page_config(page_title="Dhan Official SDK Auth Diagnostic", layout="centered")

st.title("🧪 Dhan Official SDK Diagnostic")
st.info(
    "The production bot does not use DhanLogin.generate_token(), PIN/TOTP, or automatic "
    "token generation. This page is retained as a configuration diagnostic only."
)

st.subheader("Production authentication model")
st.write("Automatic SDK token generation: **DISABLED**")
st.write("PIN/TOTP authentication: **DISABLED**")
st.write("Manual credential source: **DHAN_API_KEY**")
st.write(f"Client ID configured: **{'YES' if settings.dhan_client_id else 'NO'}**")
st.write(f"Manual credential configured: **{'YES' if settings.dhan_api_key else 'NO'}**")
st.write(f"Credential length: **{len(settings.dhan_api_key)}**")
st.write(f"Credential classification: **{settings.dhan_manual_credential_kind}**")

if settings.dhan_manual_credential_kind == "APP_KEY_NOT_ACCESS_TOKEN":
    st.error(
        "❌ The configured 8-character DHAN_API_KEY is an application/API key, not a "
        "bearer access credential. It is intentionally rejected by the production broker."
    )
elif settings.dhan_manual_credential_kind == "MANUAL_ACCESS_CREDENTIAL":
    st.success(
        "✅ A manually entered access credential is configured. The production broker "
        "can use it without SDK token generation."
    )
else:
    st.error("❌ No manually entered Dhan access credential is configured.")

st.divider()
st.subheader("Why SDK token generation is not available here")
st.write(
    "The previous diagnostic called DhanLogin.generate_token() with PIN/TOTP. That is "
    "not part of the current production authentication contract, so the page no longer "
    "makes that request or creates an access token."
)
st.warning(
    "Use the GitHub Actions Dhan preflight for the real authentication and market-feed "
    "test. No token is generated, displayed, or persisted by this diagnostic."
)

st.caption("Security: credential values, PIN, TOTP, OTP, and access tokens are never displayed or persisted.")
