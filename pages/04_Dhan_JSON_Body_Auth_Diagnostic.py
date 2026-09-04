from __future__ import annotations

import streamlit as st

from intraday_bot.config import settings

st.set_page_config(page_title="Dhan JSON Body Auth Diagnostic", layout="centered")
st.title("🧪 Dhan JSON-Body Auth Diagnostic")
st.info(
    "This page no longer performs Access Token generation. It verifies the production "
    "credential model without sending PIN/TOTP or creating a token."
)

st.subheader("Production authentication model")
st.write("Automatic token generation: **DISABLED**")
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
    st.success("✅ A manually entered access credential is configured for the production broker.")
else:
    st.error("❌ No manually entered Dhan access credential is configured.")

st.divider()
st.warning(
    "This diagnostic deliberately does not call Dhan's token-generation endpoint. "
    "The real authentication and market-feed check is performed by the GitHub Actions "
    "Dhan preflight."
)
st.caption("Security: no credential value, PIN, TOTP, OTP, or access-token value is displayed or persisted.")
