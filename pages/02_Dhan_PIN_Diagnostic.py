from __future__ import annotations

import hashlib
import os
from typing import Any

import streamlit as st


st.set_page_config(page_title="Dhan PIN Handling Diagnostic", layout="centered")

st.title("🔎 Dhan PIN Handling Diagnostic")
st.info(
    "This diagnostic checks how DHAN_PIN is being read and normalized. "
    "It never displays the PIN and never sends an authentication request to Dhan."
)


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12].upper()


def inspect_value(label: str, value: Any) -> dict[str, Any]:
    text = "" if value is None else str(value)
    stripped = text.strip()
    return {
        "source": label,
        "python_type": type(value).__name__,
        "raw_length": len(text),
        "stripped_length": len(stripped),
        "raw_is_6_digits": len(text) == 6 and text.isdigit(),
        "stripped_is_6_digits": len(stripped) == 6 and stripped.isdigit(),
        "has_leading_or_trailing_whitespace": text != stripped,
        "raw_fingerprint": fingerprint(text) if text else "EMPTY",
        "stripped_fingerprint": fingerprint(stripped) if stripped else "EMPTY",
    }


try:
    env_present = "DHAN_PIN" in os.environ
    env_value = os.environ.get("DHAN_PIN", "")
except Exception:
    env_present = False
    env_value = ""

try:
    secret_present = "DHAN_PIN" in st.secrets
    secret_value = st.secrets.get("DHAN_PIN", "")
except Exception:
    secret_present = False
    secret_value = ""

st.subheader("Credential source checks")
st.write({
    "Environment DHAN_PIN present": env_present,
    "Streamlit Secrets DHAN_PIN present": secret_present,
})

rows = []
if env_present:
    rows.append(inspect_value("Environment", env_value))
if secret_present:
    rows.append(inspect_value("Streamlit Secrets", secret_value))

configured_value = env_value.strip() if env_present and env_value.strip() else str(secret_value).strip()
configured = inspect_value("Effective configured value", configured_value)

st.subheader("Safe PIN diagnostics")
st.dataframe(rows + [configured], use_container_width=True, hide_index=True)

st.subheader("Interpretation")
checks = [
    ("Effective PIN is exactly 6 numeric characters", configured["stripped_is_6_digits"]),
    ("Effective value has no leading/trailing whitespace", not configured["has_leading_or_trailing_whitespace"]),
    ("Effective value is represented as a string", configured["python_type"] == "str"),
]
for label, ok in checks:
    st.write(("✅ " if ok else "❌ ") + label)

st.caption(
    "Fingerprints are one-way SHA-256 prefixes and are provided only to detect whether two hidden values differ. "
    "No PIN value, authentication request, OTP, or access token is shown or persisted."
)
