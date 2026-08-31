"""Always-on 24/7 market worker with a real liveness heartbeat.

The worker is deliberately independent from Streamlit. It keeps running outside
market hours, updates a heartbeat frequently, and runs the complete trading
cycle every configured interval during the NSE cash-equity window.

Paper trading remains the default and LIVE_TEST remains broker-safe simulated
execution. This process never enables real Dhan order submission.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import sys
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

_STOP = False


def now_ist() -> datetime:
    return datetime.now(IST)


def _atomic_write(payload: dict) -> None:
    HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
    tmp = HEARTBEAT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(HEARTBEAT)


def write_heartbeat(
    state: str,
    message: str = "",
    *,
    started_at: str | None = None,
    last_cycle_started_at: str | None = None,
    last_cycle_ended_at: str | None = None,
    last_cycle_id: str | None = None,
    last_cycle_errors: int = 0,
    last_cycle_duration_seconds: float | None = None,
    last_error: str = "",
) -> None:
    now = now_ist()
    _atomic_write(
        {
            "state": state,
            "message": message,
            "updated_at": now.isoformat(),
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "worker_started_at": started_at,
            "last_cycle_started_at": last_cycle_started_at,
            "last_cycle_ended_at": last_cycle_ended_at,
            "last_cycle_id": last_cycle_id,
            "last_cycle_errors": int(last_cycle_errors),
            "last_cycle_duration_seconds": last_cycle_duration_seconds,
            "last_error": last_error,
            "market_open": market_open(),
            "cycle_interval_seconds": CYCLE_INTERVAL,
            "heartbeat_interval_seconds": HEARTBEAT_INTERVAL,
            "heartbeat_stale_after_seconds": STALE_AFTER,
        }
    )


def _handle_stop(signum: int, _frame) -> None:
    global _STOP
    _STOP = True
    try:
        write_heartbeat("STOPPING", f"Received signal {signum}")
    except Exception:
        pass


def main() -> None:
    global _STOP
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    started = now_ist().isoformat()
    last_cycle_started: str | None = None
    last_cycle_ended: str | None = None
    last_cycle_id: str | None = None
    last_cycle_errors = 0
    last_cycle_duration: float | None = None
    last_error = ""
    next_cycle = time.monotonic()
    next_heartbeat = time.monotonic()

    write_heartbeat(
        "STARTING",
        "Worker process started; waiting for first market-cycle decision",
        started_at=started,
    )

    while not _STOP:
        now_mono = time.monotonic()

        if now_mono >= next_cycle:
            if market_open():
                last_cycle_started = now_ist().isoformat()
                write_heartbeat(
                    "RUNNING",
                    "Starting market cycle",
                    started_at=started,
                    last_cycle_started_at=last_cycle_started,
                    last_cycle_ended_at=last_cycle_ended,
                    last_cycle_id=last_cycle_id,
                    last_cycle_errors=last_cycle_errors,
                    last_cycle_duration_seconds=last_cycle_duration,
                    last_error=last_error,
                )
                cycle_start = time.monotonic()
                try:
                    result = run_cycle()
                    last_cycle_duration = round(time.monotonic() - cycle_start, 3)
                    last_cycle_ended = result.get("ended_at") or now_ist().isoformat()
                    last_cycle_id = result.get("cycle_id")
                    last_cycle_errors = len(result.get("errors", []))
                    last_error = " | ".join(str(x) for x in result.get("errors", []))
                    state = "RUNNING" if not result.get("errors") else "DEGRADED"
                    write_heartbeat(
                        state,
                        f"Last cycle {last_cycle_ended}; errors={last_cycle_errors}",
                        started_at=started,
                        last_cycle_started_at=last_cycle_started,
                        last_cycle_ended_at=last_cycle_ended,
                        last_cycle_id=last_cycle_id,
                        last_cycle_errors=last_cycle_errors,
                        last_cycle_duration_seconds=last_cycle_duration,
                        last_error=last_error,
                    )
                except Exception as exc:
                    last_cycle_duration = round(time.monotonic() - cycle_start, 3)
                    last_cycle_ended = now_ist().isoformat()
                    last_cycle_errors = 1
                    last_error = str(exc)
                    write_heartbeat(
                        "DEGRADED",
                        f"Cycle exception: {exc}",
                        started_at=started,
                        last_cycle_started_at=last_cycle_started,
                        last_cycle_ended_at=last_cycle_ended,
                        last_cycle_id=last_cycle_id,
                        last_cycle_errors=last_cycle_errors,
                        last_cycle_duration_seconds=last_cycle_duration,
                        last_error=last_error,
                    )
            else:
                write_heartbeat(
                    "RUNNING",
                    "Outside NSE market window; heartbeat-only mode",
                    started_at=started,
                    last_cycle_started_at=last_cycle_started,
                    last_cycle_ended_at=last_cycle_ended,
                    last_cycle_id=last_cycle_id,
                    last_cycle_errors=last_cycle_errors,
                    last_cycle_duration_seconds=last_cycle_duration,
                    last_error=last_error,
                )

            # Keep the requested five-minute cadence without accumulating drift.
            next_cycle += CYCLE_INTERVAL
            if next_cycle < time.monotonic():
                next_cycle = time.monotonic() + CYCLE_INTERVAL

        if time.monotonic() >= next_heartbeat:
            # A heartbeat update is intentionally independent of the market-cycle
            # cadence, so the UI can distinguish a live worker from stale state.
            write_heartbeat(
                "RUNNING" if not last_error else "DEGRADED",
                "Heartbeat OK" if not last_error else f"Last cycle error: {last_error}",
                started_at=started,
                last_cycle_started_at=last_cycle_started,
                last_cycle_ended_at=last_cycle_ended,
                last_cycle_id=last_cycle_id,
                last_cycle_errors=last_cycle_errors,
                last_cycle_duration_seconds=last_cycle_duration,
                last_error=last_error,
            )
            next_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL

        time.sleep(min(1.0, max(0.1, next_heartbeat - time.monotonic())))

    write_heartbeat(
        "STOPPED",
        "Worker process stopped",
        started_at=started,
        last_cycle_started_at=last_cycle_started,
        last_cycle_ended_at=last_cycle_ended,
        last_cycle_id=last_cycle_id,
        last_cycle_errors=last_cycle_errors,
        last_cycle_duration_seconds=last_cycle_duration,
        last_error=last_error,
    )


if __name__ == "__main__":
    main()
