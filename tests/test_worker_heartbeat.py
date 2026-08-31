import json

import scripts.worker as worker


def test_heartbeat_is_atomic_and_contains_liveness_fields(tmp_path, monkeypatch):
    heartbeat_path = tmp_path / "worker_heartbeat.json"
    monkeypatch.setattr(worker, "HEARTBEAT", heartbeat_path)
    monkeypatch.setattr(worker, "market_open", lambda: False)

    worker.write_heartbeat(
        "RUNNING",
        "Heartbeat OK",
        started_at="2026-08-31T17:00:00+05:30",
        last_cycle_started_at="2026-08-31T17:00:00+05:30",
        last_cycle_ended_at="2026-08-31T17:00:20+05:30",
        last_cycle_id="cycle-1",
        last_cycle_errors=0,
        last_cycle_duration_seconds=20.0,
    )

    payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    assert payload["state"] == "RUNNING"
    assert payload["message"] == "Heartbeat OK"
    assert payload["market_open"] is False
    assert payload["last_cycle_id"] == "cycle-1"
    assert payload["last_cycle_errors"] == 0
    assert payload["cycle_interval_seconds"] == 300
    assert payload["heartbeat_interval_seconds"] == 30
    assert payload["heartbeat_stale_after_seconds"] >= 90
    assert not heartbeat_path.with_suffix(".tmp").exists()


def test_heartbeat_interval_is_independent_of_cycle_interval():
    assert worker.HEARTBEAT_INTERVAL < worker.CYCLE_INTERVAL
