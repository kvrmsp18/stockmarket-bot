from __future__ import annotations

import hashlib
import os
from typing import Any

import streamlit as st


st.set_page_config(page_title="Dhan Credential Pair Diagnostic", layout="centered")

st.title("🔐 Dhan Credential Pair Diagnostic")
st.info(
    "This diagnostic checks whether the Client ID and PIN used by the application "
    "come from consistent sources and whether their hidden values match. It never "
    "displays credentials and never sends an authentication request to Dhan."
)


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12].upper()


def inspect(label: str, value: Any, expected_numeric: bool = False) -> dict[str, Any]:
    text = "" if value is None else str(value)
    stripped = text.strip()
    row = {
        "source": label,
        "python_type": type(value).__name__,
        "raw_length": len(text),
        "stripped_length": len(stripped),
        "has_leading_or_trailing_whitespace": text != stripped,
        "raw_fingerprint": fingerprint(text) if text else "EMPTY",
        "stripped_fingerprint": fingerprint(stripped) if stripped else "EMPTY",
    }
    if expected_numeric:
        row["stripped_is_6_digits"] = len(stripped) == 6 and stripped.isdigit()
    return row


def read_source(name: str) -> tuple[bool, Any]:
    try:
        if name in os.environ and str(os.environ.get(name, "")).strip():
            return True, os.environ.get(name, "")
    except Exception:
        pass
    try:
        if name in st.secrets:
            value = st.secrets.get(name, "")
            if value is not None and str(value).strip():
                return True, value
    except Exception:
        pass
    return False, ""


client_env_present = "DHAN_CLIENT_ID" in os.environ
pin_env_present = "DHAN_PIN" in os.environ
client_env = os.environ.get("DHAN_CLIENT_ID", "")
pin_env = os.environ.get("DHAN_PIN", "")

try:
    client_secret_present = "DHAN_CLIENT_ID" in st.secrets
    client_secret = st.secrets.get("DHAN_CLIENT_ID", "") if client_secret_present else ""
except Exception:
    client_secret_present = False
    client_secret = ""

try:
    pin_secret_present = "DHAN_PIN" in st.secrets
    pin_secret = st.secrets.get("DHAN_PIN", "") if pin_secret_present else ""
except Exception:
    pin_secret_present = False
    pin_secret = ""

st.subheader("Source availability")
st.write({
    "Environment Client ID present": client_env_present,
    "Streamlit Client ID present": client_secret_present,
    "Environment PIN present": pin_env_present,
    "Streamlit PIN present": pin_secret_present,
})

rows: list[dict[str, Any]] = []
if client_env_present:
    rows.append(inspect("Environment / Client ID", client_env))
if client_secret_present:
    rows.append(inspect("Streamlit Secrets / Client ID", client_secret))
if pin_env_present:
    rows.append(inspect("Environment / PIN", pin_env, expected_numeric=True))
if pin_secret_present:
    rows.append(inspect("Streamlit Secrets / PIN", pin_secret, expected_numeric=True))

st.subheader("Safe credential diagnostics")
st.dataframe(rows, use_container_width=True, hide_index=True)

st.subheader("Effective source selection")
st.info(
    "The application's configuration prefers a non-empty Environment value over "
    "Streamlit Secrets for credential fields."
)

if client_env_present and client_secret_present:
    same = inspect("Environment / Client ID", client_env)["raw_fingerprint"] == inspect(
        "Streamlit Secrets / Client ID", client_secret
    )["raw_fingerprint"]
    st.success("✅ Environment and Streamlit Client ID match.") if same else st.error(
        "❌ Environment and Streamlit Client ID are different hidden values."
    )

if pin_env_present and pin_secret_present:
    same = inspect("Environment / PIN", pin_env)["raw_fingerprint"] == inspect(
        "Streamlit Secrets / PIN", pin_secret
    )["raw_fingerprint"]
    st.success("✅ Environment and Streamlit PIN match.") if same else st.error(
        "❌ Environment and Streamlit PIN are different hidden values."
    )

st.subheader("Client ID + PIN readiness")
client_effective = str(client_env).strip() if client_env_present and str(client_env).strip() else str(client_secret).strip()
pin_effective = str(pin_env).strip() if pin_env_present and str(pin_env).strip() else str(pin_secret).strip()

ready = bool(client_effective) and len(pin_effective) == 6 and pin_effective.isdigit()
if ready:
    st.success("✅ Effective Client ID is present and effective PIN has the expected 6-digit numeric format.")
else:
    st.error("❌ Effective credential pair is not structurally ready.")

st.caption(
    "Security: only lengths, types, whitespace state and one-way SHA-256 prefixes are shown. "
    "No credential, OTP, token, or authentication request is exposed or persisted."
)
