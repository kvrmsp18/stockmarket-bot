from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import requests

try:
    import pyotp
except ImportError:  # pragma: no cover
    pyotp = None  # type: ignore[assignment]


_AUTH_BASE_URL = "https://auth.dhan.co"
_token_lock = threading.Lock()
_cached_token = ""
_CACHE_PATH = Path(os.getenv("DHAN_RUNTIME_TOKEN_CACHE", "/tmp/stockmarket-bot-dhan-token.json"))
_CACHE_MAX_AGE_SECONDS = 15 * 60


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


def _read_runtime_cache() -> str:
    try:
        payload = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        token = _clean(str(payload.get("access_token", "")))
        created_at = float(payload.get("created_at", 0))
        if token and created_at and (time.time() - created_at) <= _CACHE_MAX_AGE_SECONDS:
            return token
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return ""


def _write_runtime_cache(token: str) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"access_token": token, "created_at": time.time()}),
            encoding="utf-8",
        )
        os.replace(tmp, _CACHE_PATH)
    except OSError:
        # Cache is only an intra-job optimisation; authentication must still work
        # when the temporary filesystem is unavailable.
        pass


def generate_access_token(client_id: str, pin: str, totp_secret: str, timeout: int = 15) -> str:
    """Generate or reuse a Dhan Access Token using Client ID + PIN + TOTP seed.

    The temporary runtime cache is shared across workflow steps in the same
    GitHub Actions job. This prevents a second Dhan token-generation request
    inside the same job, which Dhan can reject with its two-minute generation
    limit. The cache is never written into the repository.
    """
    global _cached_token
    client_id = _clean(client_id)
    pin = _clean(pin)
    totp_secret = _normalise_totp_secret(totp_secret)
    if not client_id or not pin or not totp_secret:
        raise RuntimeError(
            "DHAN_AUTO_AUTH_UNAVAILABLE: DHAN_CLIENT_ID, DHAN_PIN and DHAN_TOTP_SECRET are required"
        )
    if pyotp is None:
        raise RuntimeError("DHAN_AUTO_AUTH_DEPENDENCY_MISSING: install pyotp")

    with _token_lock:
        if _cached_token:
            return _cached_token

        cached = _read_runtime_cache()
        if cached:
            _cached_token = cached
            return cached

        # pyotp.now() uses the host's synchronized Unix clock and produces the
        # standard 6-digit RFC 6238 TOTP used by Dhan's authentication endpoint.
        totp_code = pyotp.TOTP(totp_secret).now()

        response = requests.post(
            f"{_AUTH_BASE_URL}/app/generateAccessToken",
            params={"dhanClientId": client_id, "pin": pin, "totp": totp_code},
            timeout=timeout,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text[:500]}

        if isinstance(payload, dict):
            status = str(payload.get("status", "")).lower()
            if status in {"failure", "failed", "error"}:
                raise RuntimeError(f"DHAN_AUTO_AUTH_FAILED: {_safe_auth_message(payload)}")

        if response.status_code >= 400:
            raise RuntimeError(
                f"DHAN_AUTO_AUTH_HTTP_{response.status_code}: {_safe_auth_message(payload)}"
            )

        token = _extract_access_token(payload)
        if not token:
            raise RuntimeError(f"DHAN_AUTO_AUTH_NO_TOKEN: {_safe_auth_message(payload)}")
        _cached_token = token
        _write_runtime_cache(token)
        return token


def get_access_token(
    client_id: str,
    pin: str,
    totp_secret: str,
    manual_access_token: str = "",
) -> tuple[str, str]:
    """Return (token, source), preferring an explicitly supplied runtime token."""
    manual_access_token = _clean(manual_access_token)
    if manual_access_token:
        return manual_access_token, "DHAN_ACCESS_TOKEN"
    return generate_access_token(client_id, pin, totp_secret), "DHAN_PIN_TOTP"


def clear_cached_token() -> None:
    global _cached_token
    with _token_lock:
        _cached_token = ""
    try:
        _CACHE_PATH.unlink(missing_ok=True)
    except OSError:
        pass
