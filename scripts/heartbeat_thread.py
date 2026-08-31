from __future__ import annotations

import json
import os
import socket
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def write_heartbeat(path: Path, state: dict) -> None:
    payload = dict(state)
    payload["updated_at"] = datetime.now(IST).isoformat()
    payload["pid"] = os.getpid()
    payload["hostname"] = socket.gethostname()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def run(stop_event, path: Path, state_getter, interval: int) -> None:
    while not stop_event.is_set():
        try:
            write_heartbeat(path, state_getter())
        except Exception:
            pass
        stop_event.wait(interval)
