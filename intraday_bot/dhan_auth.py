from __future__ import annotations

import email.utils
import threading
import time
from datetime import timezone
from typing import Any

import requests

try:
    import pyotp
except ImportError:  # pragma: no cover
    pyotp = None  # type: ignore[assignment]


_AUTH_BASE_URL = "https://auth.dhan.co"
_token_lock = threading.Lock()
_cached_token = ""


def _clean(value: str | None) -> str:
    value = (value or "").strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"', "`"}:
        value = value[1:-1].strip()
    return value


def _normalise_totp_secret(value: str) -> str:
    value = _clean(value)
    return "".join(value.split()).replace("-", "").upper()


def _extract_access_token(payload: Any) -> str:
    if isinstance(payload, dict):
        candidates: list[Any] = [payload.get("accessToken"), payload.get("access_token")]
        data = payload.get("data")
        if isinstance(data, dict):
            candidates.extend([data.get("accessToken"), data.get("access_token")])
        for value in candidates:
            token = _clean(str(value)) if value is not None else ""
            if token:
                return token
    return ""


def _safe_auth_message(payload: Any) -> str:
    """Extract only non-secret authentication diagnostics from Dhan's response."""
    if not isinstance(payload, dict):
        return str(payload)[:500]
    message = payload.get("message")
    remarks = payload.get("remarks")
    if isinstance(remarks, dict):
        message = remarks.get("message") or remarks.get("error_message") or message
        error_type = remarks.get("error_type")
        if error_type and message:
            return f"{error_type}: {message}"
    return str(message or payload.get("status") or payload)[:500]


def _dhan_server_clock_offset(timeout: int = 8) -> float:
    """Estimate Dhan auth-server clock minus runner clock from HTTP Date."""
    try:
        response = requests.get(_AUTH_BASE_URL, timeout=timeout, allow_redirects=False)
        date_header = response.headers.get("Date", "")
        if not date_header:
            return 0.0
        server_dt = email.utils.parsedate_to_datetime(date_header)
        if server_dt.tzinfo is None:
            server_dt = server_dt.replace(tzinfo=timezone.utc)
        return server_dt.timestamp() - time.time()
    except Exception:
        return 0.0


def generate_access_token(client_id: str, pin: str, totp_secret: str, timeout: int = 15) -> str:
    """Generate a fresh Dhan Access Token using Client ID + PIN + TOTP seed."""
    global _cached_token
    client_id = _clean(client_id)
    pin = _clean(pin)
    totp_secret = _normalise_totp_secret(totp_secret)
    if not client_id or not pin or not totp_secret:
        raise RuntimeError("DHAN_AUTO_AUTH_UNAVAILABLE: DHAN_CLIENT_ID, DHAN_PIN and DHAN_TOTP_SECRET are required")
    if pyotp is None:
        raise RuntimeError("DHAN_AUTO_AUTH_DEPENDENCY_MISSING: install pyotp")

    with _token_lock:
        if _cached_token:
            return _cached_token

        offset = _dhan_server_clock_offset()
        effective_time = time.time() + offset
        totp_code = pyotp.TOTP(totp_secret).at(int(effective_time))

        response = requests.post(
            f"{_AUTH_BASE_URL}/app/generateAccessToken",
            params={"dhanClientId": client_id, "pin": pin, "totp": totp_code},
            timeout=timeout,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text[:500]}

        # Dhan can return HTTP 200 with a failure status, so inspect the
        # response body instead of assuming that HTTP 200 means success.
        if isinstance(payload, dict):
            status = str(payload.get("status", "")).lower()
            if status in {"failure", "failed", "error"}:
                raise RuntimeError(f"DHAN_AUTO_AUTH_FAILED: {_safe_auth_message(payload)}")

        if response.status_code >= 400:
            raise RuntimeError(f"DHAN_AUTO_AUTH_HTTP_{response.status_code}: {_safe_auth_message(payload)}")

        token = _extract_access_token(payload)
        if not token:
            raise RuntimeError(f"DHAN_AUTO_AUTH_NO_TOKEN: {_safe_auth_message(payload)}")
        _cached_token = token
        return token


def get_access_token(client_id: str, pin: str, totp_secret: str, manual_access_token: str = "") -> tuple[str, str]:
    """Return (token, source), preferring an explicitly supplied runtime token."""
    manual_access_token = _clean(manual_access_token)
    if manual_access_token:
        return manual_access_token, "DHAN_ACCESS_TOKEN"
    return generate_access_token(client_id, pin, totp_secret), "DHAN_PIN_TOTP"


def clear_cached_token() -> None:
    global _cached_token
    with _token_lock:
        _cached_token = ""
