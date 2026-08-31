"""Always-on 24/7 market worker.

The worker is independent from Streamlit. A dedicated heartbeat thread writes
liveness every configured interval even while a market/research cycle is running.
Paper trading is the default and LIVE_TEST uses simulated fills only.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from intraday_bot.runtime import market_open, run_cycle

IST = ZoneInfo("Asia/Kolkata")
HEARTBEAT = ROOT / "data" / "worker_heartbeat.json"
CYCLE_INTERVAL = max(60, int(os.getenv("WORKER_INTERVAL_SECONDS", "300")))
HEARTBEAT_INTERVAL = max(10, min(60, int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "30"))))
STALE_AFTER = max(90, HEARTBEAT_INTERVAL * 3)

_STOP = threading.Event()
_LOCK = threading.Lock()
_STATE: dict[str, object] = {
    "state": "STARTING",
    "message": "Worker starting",
    "worker_started_at": None,
    "last_cycle_started_at": None,
    "last_cycle_ended_at": None,
    "last_cycle_id": None,
    "last_cycle_errors": 0,
    "last_cycle_duration_seconds": None,
    "last_error": "",
}


def now_ist() -> datetime:
    return datetime.now(IST)


def _snapshot() -> dict[str, object]:
    with _LOCK:
        return dict(_STATE)


def _set(**changes: object) -> None:
    with _LOCK:
        _STATE.update(changes)


def _atomic_write(payload: dict) -> None:
    HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
    tmp = HEARTBEAT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(HEARTBEAT)


def write_heartbeat(
    state: str | None = None,
    message: str | None = None,
    *,
    started_at: str | None = None,
    last_cycle_started_at: str | None = None,
    last_cycle_ended_at: str | None = None,
    last_cycle_id: str | None = None,
    last_cycle_errors: int | None = None,
    last_cycle_duration_seconds: float | None = None,
    last_error: str | None = None,
) -> None:
    """Persist a heartbeat snapshot.

    The optional arguments preserve compatibility with the original heartbeat
    writer API while the normal worker path uses the shared state snapshot.
    This makes direct health tests and the independent heartbeat thread exercise
    the same atomic persistence path.
    """
    changes: dict[str, object] = {}
    if state is not None:
        changes["state"] = state
    if message is not None:
        changes["message"] = message
    if started_at is not None:
        changes["worker_started_at"] = started_at
    if last_cycle_started_at is not None:
        changes["last_cycle_started_at"] = last_cycle_started_at
    if last_cycle_ended_at is not None:
        changes["last_cycle_ended_at"] = last_cycle_ended_at
    if last_cycle_id is not None:
        changes["last_cycle_id"] = last_cycle_id
    if last_cycle_errors is not None:
        changes["last_cycle_errors"] = int(last_cycle_errors)
    if last_cycle_duration_seconds is not None:
        changes["last_cycle_duration_seconds"] = last_cycle_duration_seconds
    if last_error is not None:
        changes["last_error"] = last_error
    if changes:
        _set(**changes)

    payload = _snapshot()
    payload.update(
        {
            "updated_at": now_ist().isoformat(),
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "market_open": market_open(),
            "cycle_interval_seconds": CYCLE_INTERVAL,
            "heartbeat_interval_seconds": HEARTBEAT_INTERVAL,
            "heartbeat_stale_after_seconds": STALE_AFTER,
        }
    )
    _atomic_write(payload)


def heartbeat_loop() -> None:
    while not _STOP.is_set():
        try:
            write_heartbeat()
        except Exception:
            pass
        _STOP.wait(HEARTBEAT_INTERVAL)


def _handle_stop(signum: int, _frame) -> None:
    _set(state="STOPPING", message=f"Received signal {signum}")
    try:
        write_heartbeat()
    finally:
        _STOP.set()


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    _set(
        state="RUNNING",
        message="Worker started; independent heartbeat active",
        worker_started_at=now_ist().isoformat(),
    )
    write_heartbeat()

    hb = threading.Thread(target=heartbeat_loop, name="worker-heartbeat", daemon=True)
    hb.start()
    next_cycle = time.monotonic()

    while not _STOP.is_set():
        if time.monotonic() >= next_cycle:
            if market_open():
                started = now_ist().isoformat()
                _set(
                    state="RUNNING",
                    message="Market cycle running; heartbeat remains active",
                    last_cycle_started_at=started,
                )
                try:
                    result = run_cycle()
                    errors = result.get("errors", [])
                    ended = result.get("ended_at") or now_ist().isoformat()
                    _set(
                        state="DEGRADED" if errors else "RUNNING",
                        message=f"Last cycle {ended}; errors={len(errors)}",
                        last_cycle_ended_at=ended,
                        last_cycle_id=result.get("cycle_id"),
                        last_cycle_errors=len(errors),
                        last_cycle_duration_seconds=result.get("duration_seconds"),
                        last_error=" | ".join(str(x) for x in errors),
                    )
                except Exception as exc:
                    _set(
                        state="DEGRADED",
                        message=f"Cycle exception: {exc}",
                        last_cycle_ended_at=now_ist().isoformat(),
                        last_cycle_errors=1,
                        last_error=str(exc),
                    )
            else:
                _set(
                    state="RUNNING",
                    message="Outside NSE market window; heartbeat-only mode",
                )
            try:
                write_heartbeat()
            except Exception:
                pass
            next_cycle += CYCLE_INTERVAL
            if next_cycle < time.monotonic():
                next_cycle = time.monotonic() + CYCLE_INTERVAL
        _STOP.wait(1.0)

    _set(state="STOPPED", message="Worker process stopped")
    write_heartbeat()
    hb.join(timeout=HEARTBEAT_INTERVAL + 1)


if __name__ == "__main__":
    main()
