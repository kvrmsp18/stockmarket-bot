"""Read-only live market-data smoke check for DhanHQ.

This script performs one live LTP poll using the configured official Dhan
exchange/security IDs. It never places orders and never prints credentials.
"""

from __future__ import annotations

import json
import os
import sys

from .dhan_api import DhanHQClient, load_security_map_from_env
from .market_data import DhanMarketDataService, MarketDataError


def _requested_symbols() -> tuple[str, ...] | None:
    """Return an optional comma-separated symbol selection from the environment."""
    raw = os.getenv("DHAN_LIVE_SYMBOLS", "").strip()
    if not raw:
        return None

    symbols = tuple(symbol.strip().upper() for symbol in raw.split(",") if symbol.strip())
    return symbols or None


def main() -> int:
    """Run one safe live market-data poll and print a non-secret result."""
    try:
        security_map = load_security_map_from_env()
        if not security_map:
            raise MarketDataError(
                "No instruments configured. Set DHAN_SECURITY_IDS_JSON with official Dhan IDs."
            )

        client = DhanHQClient()
        service = DhanMarketDataService(client, security_map)
        result = service.poll(_requested_symbols())

        output = {
            "status": "READY",
            "provider": "dhanhq",
            "read_only": True,
            "received_at": result.received_at.isoformat(),
            "snapshots": [
                {
                    "symbol": snapshot.symbol,
                    "exchange": snapshot.exchange,
                    "timestamp": snapshot.timestamp.isoformat(),
                    "last_price": snapshot.last_price,
                    "previous_close": snapshot.previous_close,
                    "volume": snapshot.volume,
                    "bid": snapshot.bid,
                    "ask": snapshot.ask,
                }
                for snapshot in result.snapshots
            ],
        }
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0
    except (MarketDataError, RuntimeError, ValueError, KeyError) as exc:
        print(
            json.dumps(
                {
                    "status": "ERROR",
                    "provider": "dhanhq",
                    "read_only": True,
                    "error": str(exc),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
