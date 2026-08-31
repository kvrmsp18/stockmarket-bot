"""Always-on 24/7 monitoring worker."""
from __future__ import annotations

import json
import os
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
INTERVAL = max(60, int(os.getenv("WORKER_INTERVAL_SECONDS", "300")))


def write_heartbeat(state: str, message: str = "") -> None:
    HEARTBEAT.parent.mkdir(exist_ok=True)
    now = datetime.now(IST)
    HEARTBEAT.write_text(json.dumps({"state": state, "message": message, "updated_at": now.isoformat(), "pid": os.getpid(), "interval_seconds": INTERVAL}, indent=2), encoding="utf-8")


def main() -> None:
    write_heartbeat("STARTING")
    while True:
        try:
            if market_open():
                write_heartbeat("RUNNING", "Starting market cycle")
                result = run_cycle()
                write_heartbeat("RUNNING", f"Last cycle {result.get('ended_at')} errors={len(result.get('errors', []))}")
            else:
                write_heartbeat("RUNNING", "Outside NSE market window; heartbeat only")
        except Exception as exc:
            write_heartbeat("ERROR", str(exc))
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
