"""Always-on worker.

The process stays alive 24/7. During NSE cash-equity hours it runs the complete
paper cycle every configured interval. Outside market hours it writes a
heartbeat so the service can be monitored without fabricating market data.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from intraday_bot.runtime import market_open, run_cycle

IST = ZoneInfo("Asia/Kolkata")
HEARTBEAT = Path("data/worker_heartbeat.json")
INTERVAL = max(60, int(os.getenv("WORKER_INTERVAL_SECONDS", "300")))


def write_heartbeat(state: str, message: str = "") -> None:
    HEARTBEAT.parent.mkdir(exist_ok=True)
    now = datetime.now(IST)
    payload = {"state": state, "message": message, "updated_at": now.isoformat(), "pid": os.getpid(), "interval_seconds": INTERVAL}
    HEARTBEAT.write_text(json.dumps(payload, indent=2), encoding="utf-8")


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
