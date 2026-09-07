from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def age_seconds(value: Any, now: datetime | None = None) -> float | None:
    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0.0, (current.astimezone(timezone.utc) - parsed).total_seconds())


def heartbeat_check(name: str, payload: dict[str, Any], max_age_seconds: int = 900, now: datetime | None = None) -> dict[str, Any]:
    age = age_seconds(payload.get("updated_at"), now)
    fresh = age is not None and age <= max_age_seconds
    return {
        "component": name,
        "status": "PASS" if fresh else "FAIL",
        "fresh": fresh,
        "age_seconds": round(age, 1) if age is not None else None,
        "state": payload.get("state", "DATA UNAVAILABLE"),
        "cycle_success": payload.get("cycle_success"),
        "market_open": payload.get("market_open"),
        "updated_at": payload.get("updated_at"),
        "reason": "fresh heartbeat" if fresh else "heartbeat missing or stale",
    }


def preflight_check(payload: dict[str, Any], max_age_seconds: int = 1800, now: datetime | None = None) -> dict[str, Any]:
    age = age_seconds(payload.get("updated_at"), now)
    passed = str(payload.get("status", "")).upper() == "PASSED"
    live_submission = payload.get("live_order_submission") is True
    fresh = age is not None and age <= max_age_seconds
    ok = passed and fresh and not live_submission
    return {
        "component": "Dhan preflight",
        "status": "PASS" if ok else "FAIL",
        "fresh": fresh,
        "passed": passed,
        "live_order_submission": live_submission,
        "age_seconds": round(age, 1) if age is not None else None,
        "updated_at": payload.get("updated_at"),
        "reason": "preflight passed and live submission is disabled" if ok else "preflight unavailable/stale or live submission flag is enabled",
    }


def evaluate_runtime_health(*, mode: str, live_enabled: bool, emergency_stop: bool, worker: dict[str, Any], scheduler: dict[str, Any], preflight: dict[str, Any], status: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Produce a truthful read-only health model from persisted runtime state."""
    checks = [
        {"component": "Operational mode", "status": "PASS" if str(mode).upper() == "PAPER" else "FAIL", "reason": "PAPER mode is active" if str(mode).upper() == "PAPER" else "runtime is not in PAPER mode"},
        {"component": "Live trading gate", "status": "PASS" if not live_enabled else "FAIL", "reason": "live order submission is disabled" if not live_enabled else "DHAN_LIVE_TRADING_ENABLED is enabled"},
        {"component": "Emergency stop", "status": "PASS" if not emergency_stop else "FAIL", "reason": "emergency stop is not active" if not emergency_stop else "emergency stop is active"},
        heartbeat_check("Worker heartbeat", worker, now=now),
        heartbeat_check("Scheduler heartbeat", scheduler, now=now),
        preflight_check(preflight, now=now),
        {"component": "Latest cycle", "status": "PASS" if status.get("cycle_success") is True else "FAIL", "reason": "latest cycle reported success" if status.get("cycle_success") is True else "latest cycle success is not verified", "cycle_id": status.get("cycle_id"), "updated_at": status.get("updated_at") or status.get("ended_at")},
    ]
    failures = [x["component"] for x in checks if x.get("status") != "PASS"]
    return {"status": "HEALTHY" if not failures else "DEGRADED", "checks": checks, "failed_components": failures, "evidence_based": True}


def file_state(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {"path": str(target), "exists": False, "size_bytes": None}
    stat = target.stat()
    return {"path": str(target), "exists": True, "size_bytes": stat.st_size, "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()}
