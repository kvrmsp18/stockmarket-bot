from __future__ import annotations

import time
from typing import Any

import pyotp
import requests
import streamlit as st

from intraday_bot.config import settings
from intraday_bot.dhan_auth import _normalise_totp_secret


st.set_page_config(page_title="Dhan Auth Request Diagnostic", layout="centered")

st.title("🧪 Dhan Auth Request Diagnostic")
st.info(
    "This test performs one real Dhan Access Token generation request using the "
    "configured Client ID + PIN + TOTP. It never displays or stores the Client ID, "
    "PIN, TOTP secret, or generated 6-digit OTP."
)

client_id = settings.dhan_client_id
pin = settings.dhan_pin
secret = settings.dhan_totp_secret

if not client_id or not pin or not secret:
    st.error("Required Dhan authentication settings are not available in Streamlit Secrets.")
    st.stop()

try:
    normalised = _normalise_totp_secret(secret)
    generator = pyotp.TOTP(normalised)
    current_code = generator.now()
    remaining = int(generator.interval - (time.time() % generator.interval))
except Exception as exc:
    st.error(f"Unable to initialise TOTP locally: {exc}")
    st.stop()

st.write(f"TOTP period: **{generator.interval}s**")
st.write(f"Current TOTP window expires in approximately **{remaining}s**")
st.caption("The generated OTP is used only for this request and is never shown.")

if st.button("Run Dhan authentication test", type="primary"):
    started = time.perf_counter()
    try:
        response = requests.post(
            "https://auth.dhan.co/app/generateAccessToken",
            params={"dhanClientId": client_id, "pin": pin, "totp": current_code},
            timeout=15,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000

        try:
            payload: Any = response.json()
        except ValueError:
            payload = {"raw": response.text[:500]}

        safe = {}
        if isinstance(payload, dict):
            for key in ("status", "message"):
                if payload.get(key) is not None:
                    safe[key] = str(payload.get(key))[:500]
            remarks = payload.get("remarks")
            if isinstance(remarks, dict):
                for key in ("error_type", "message", "error_message"):
                    if remarks.get(key) is not None:
                        safe[f"remarks.{key}"] = str(remarks.get(key))[:500]

        st.write(f"HTTP status: **{response.status_code}**")
        st.write(f"Response time: **{elapsed_ms:.0f} ms**")

        if safe:
            st.json(safe)
        else:
            st.code("Dhan returned a response without a recognized non-secret status/message field.")

        if response.status_code == 200:
            st.success("✅ Dhan accepted the authentication HTTP request. Check the response above for token-generation status.")
        elif response.status_code in {400, 401, 403}:
            st.error("❌ Dhan rejected the authentication request. The non-secret response above is the key diagnostic evidence.")
        else:
            st.warning("⚠️ Dhan returned an unexpected HTTP status. The response above is the diagnostic evidence.")

    except requests.RequestException as exc:
        st.error(f"❌ HTTP request to Dhan failed: {type(exc).__name__}")
    except Exception as exc:
        st.error(f"❌ Diagnostic failed: {type(exc).__name__}")

st.divider()
st.caption("Security: credentials and OTP are kept in memory only; no secrets, OTP values, or access tokens are displayed or persisted.")
