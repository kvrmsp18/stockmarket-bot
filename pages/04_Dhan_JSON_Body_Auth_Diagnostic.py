from __future__ import annotations

import json
import time
from typing import Any

import pyotp
import requests
import streamlit as st

from intraday_bot.config import settings
from intraday_bot.dhan_auth import _normalise_totp_secret

st.set_page_config(page_title="Dhan JSON Body Auth Diagnostic", layout="centered")
st.title("🧪 Dhan JSON-Body Auth Diagnostic")
st.info(
    "This is an AUTH-ONLY A/B test. It makes one real Dhan Access Token "
    "generation request using a JSON body instead of query parameters. "
    "No credential or OTP value is displayed or persisted."
)

client_id = settings.dhan_client_id
pin = settings.dhan_pin
secret = settings.dhan_totp_secret

if not client_id or not pin or not secret:
    st.error("Required Dhan authentication settings are not available.")
    st.stop()

try:
    totp = pyotp.TOTP(_normalise_totp_secret(secret))
    remaining = int(totp.interval - (time.time() % totp.interval))
except Exception as exc:
    st.error(f"Unable to initialise TOTP locally: {type(exc).__name__}")
    st.stop()

st.write(f"TOTP period: **{totp.interval}s**")
st.write(f"Current TOTP window expires in approximately **{remaining}s**")
st.warning(
    "Run this only after the normal Dhan Auth Request Diagnostic has been "
    "tested recently enough that Dhan's token-generation cooldown is respected."
)

if st.button("Run JSON-body Dhan authentication test", type="primary"):
    code = totp.now()
    started = time.perf_counter()
    try:
        payload = {
            "dhanClientId": client_id,
            "pin": pin,
            "totp": code,
        }
        response = requests.post(
            "https://auth.dhan.co/app/generateAccessToken",
            json=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000

        try:
            body: Any = response.json()
        except ValueError:
            body = {"raw_response": response.text[:500]}

        safe: dict[str, Any] = {}
        if isinstance(body, dict):
            for key in ("status", "message"):
                if body.get(key) is not None:
                    safe[key] = str(body.get(key))[:500]
            remarks = body.get("remarks")
            if isinstance(remarks, dict):
                for key in ("error_type", "message", "error_message"):
                    if remarks.get(key) is not None:
                        safe[f"remarks.{key}"] = str(remarks.get(key))[:500]
            if body.get("accessToken") or body.get("access_token") or body.get("token"):
                safe["token_present"] = True

        st.write(f"HTTP status: **{response.status_code}**")
        st.write(f"Response time: **{elapsed_ms:.0f} ms**")
        st.json(safe if safe else {"response": "No recognized non-secret fields returned"})

        if any(k in body for k in ("accessToken", "access_token", "token")) if isinstance(body, dict) else False:
            st.success("✅ JSON-body request produced a token field. This indicates the request format is accepted.")
        elif isinstance(body, dict) and str(body.get("message", "")).strip():
            st.error(f"❌ Dhan rejected the JSON-body request: {str(body.get('message'))[:200]}")
        else:
            st.warning("⚠️ JSON-body request returned no recognized token or error message.")

    except requests.RequestException as exc:
        st.error(f"❌ HTTP request failed: {type(exc).__name__}")
    except Exception as exc:
        st.error(f"❌ Diagnostic failed: {type(exc).__name__}")

st.divider()
st.caption(
    "Security: credentials and OTP are held in memory only. No secret, OTP, "
    "or access token value is displayed or persisted."
)
