from __future__ import annotations

import time
from typing import Any

import pyotp
import streamlit as st

from dhanhq import DhanLogin
from intraday_bot.config import settings
from intraday_bot.dhan_auth import _normalise_totp_secret


st.set_page_config(page_title="Dhan Official SDK Auth Diagnostic", layout="centered")

st.title("🧪 Dhan Official SDK Auth Diagnostic")
st.info(
    "AUTH-ONLY A/B test. This uses Dhan's official Python SDK DhanLogin.generate_token() "
    "with the configured Client ID + PIN + freshly generated TOTP. It never displays "
    "credentials, OTP values, or an access token."
)

client_id = settings.dhan_client_id
pin = settings.dhan_pin
secret = settings.dhan_totp_secret

if not client_id or not pin or not secret:
    st.error("Required Dhan authentication settings are not available.")
    st.stop()

try:
    normalised = _normalise_totp_secret(secret)
    generator = pyotp.TOTP(normalised)
    remaining = int(generator.interval - (time.time() % generator.interval))
except Exception as exc:
    st.error(f"Unable to initialise TOTP locally: {type(exc).__name__}")
    st.stop()

st.write(f"TOTP period: **{generator.interval}s**")
st.write(f"Current TOTP window expires in approximately **{remaining}s**")
st.caption("OTP is generated immediately before the SDK call and is never shown.")

if st.button("Run official Dhan SDK authentication test", type="primary"):
    started = time.perf_counter()
    try:
        # Generate immediately before the authentication call.
        current_code = generator.now()
        dhan_login = DhanLogin(client_id)
        payload: Any = dhan_login.generate_token(pin, current_code)
        elapsed_ms = (time.perf_counter() - started) * 1000

        safe: dict[str, Any] = {}
        if isinstance(payload, dict):
            for key in ("status", "message", "dhanClientId", "expiryTime", "dhanClientName", "dhanClientUcc", "givenPowerOfAttorney"):
                if key in payload:
                    value = payload[key]
                    if key == "dhanClientId":
                        safe[key] = "present"
                    else:
                        safe[key] = value
            safe["accessToken"] = "present" if payload.get("accessToken") else "absent"
        else:
            safe["response_type"] = type(payload).__name__

        st.write(f"SDK response time: **{elapsed_ms:.0f} ms**")
        st.json(safe)

        if isinstance(payload, dict) and payload.get("accessToken"):
            st.success("✅ OFFICIAL SDK AUTHENTICATION SUCCEEDED — Dhan returned an Access Token.")
            st.info("Do not use this diagnostic token for trading. This test only establishes the authentication result.")
        else:
            st.error("❌ OFFICIAL SDK AUTHENTICATION DID NOT RETURN AN ACCESS TOKEN. The non-secret response above is the diagnostic evidence.")

    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        st.write(f"SDK response time: **{elapsed_ms:.0f} ms**")
        st.error(f"❌ Official SDK authentication failed: {type(exc).__name__}")
        st.caption("No credential, OTP, or access-token value is displayed.")

st.divider()
st.caption("Security: credentials and OTP remain in memory only; no secrets or access tokens are displayed or persisted.")
