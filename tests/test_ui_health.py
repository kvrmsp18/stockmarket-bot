from __future__ import annotations
from datetime import datetime, timedelta, timezone
from intraday_bot.ui_health import evaluate_runtime_health, heartbeat_check, preflight_check

def test_heartbeat_missing_timestamp_is_not_pass() -> None:
    result = heartbeat_check("worker", {})
    assert result["status"] == "FAIL"
    assert result["fresh"] is False

def test_preflight_requires_passed_fresh_and_no_live_submission() -> None:
    now = datetime.now(timezone.utc)
    good = {"status": "PASSED", "updated_at": now.isoformat(), "live_order_submission": False}
    assert preflight_check(good, now=now)["status"] == "PASS"
    assert preflight_check(dict(good, live_order_submission=True), now=now)["status"] == "FAIL"

def test_runtime_health_fails_if_live_gate_is_enabled() -> None:
    now = datetime.now(timezone.utc)
    heartbeat = {"updated_at": now.isoformat(), "state": "RUNNING", "cycle_success": True, "market_open": True}
    preflight = {"status": "PASSED", "updated_at": now.isoformat(), "live_order_submission": False}
    result = evaluate_runtime_health(mode="PAPER", live_enabled=True, emergency_stop=False, worker=heartbeat, scheduler=heartbeat, preflight=preflight, status={"cycle_success": True, "cycle_id": "cycle-test"}, now=now)
    assert result["status"] == "DEGRADED"
    assert "Live trading gate" in result["failed_components"]

def test_runtime_health_is_healthy_only_with_all_evidence() -> None:
    now = datetime.now(timezone.utc)
    heartbeat = {"updated_at": now.isoformat(), "state": "RUNNING", "cycle_success": True, "market_open": True}
    preflight = {"status": "PASSED", "updated_at": now.isoformat(), "live_order_submission": False}
    result = evaluate_runtime_health(mode="PAPER", live_enabled=False, emergency_stop=False, worker=heartbeat, scheduler=heartbeat, preflight=preflight, status={"cycle_success": True, "cycle_id": "cycle-test"}, now=now)
    assert result["status"] == "HEALTHY"
    assert result["failed_components"] == []

def test_stale_heartbeat_is_degraded() -> None:
    now = datetime.now(timezone.utc)
    old = (now - timedelta(seconds=901)).isoformat()
    heartbeat = {"updated_at": old, "state": "RUNNING", "cycle_success": True, "market_open": True}
    preflight = {"status": "PASSED", "updated_at": now.isoformat(), "live_order_submission": False}
    result = evaluate_runtime_health(mode="PAPER", live_enabled=False, emergency_stop=False, worker=heartbeat, scheduler=heartbeat, preflight=preflight, status={"cycle_success": True, "cycle_id": "cycle-test"}, now=now)
    assert result["status"] == "DEGRADED"
    assert "Worker heartbeat" in result["failed_components"]
