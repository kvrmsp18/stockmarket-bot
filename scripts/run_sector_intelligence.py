"""Build live NSE sector intelligence for the production paper-trading cycle."""
from __future__ import annotations

import json
import sys
from pathlib import Path

# When a file under scripts/ is executed directly (``python scripts/foo.py``),
# Python puts scripts/ on sys.path rather than the repository root.  Explicitly
# add the root so the production intraday_bot package is always importable in
# GitHub Actions and local validation.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from intraday_bot.brokers import DhanBroker
from intraday_bot.sector_intelligence import build


def main() -> None:
    universe_path = ROOT / "data" / "universe.json"
    universe = json.loads(universe_path.read_text(encoding="utf-8")) if universe_path.exists() else []
    if not isinstance(universe, list) or not universe:
        raise SystemExit("SECTOR_INTELLIGENCE_FAILED: universe is empty")

    broker = DhanBroker()
    quotes = broker.bulk_quotes(universe)
    if not quotes:
        raise SystemExit("SECTOR_INTELLIGENCE_FAILED: Dhan returned zero quotes")

    result = build(universe, quotes)
    if result.get("status") != "AVAILABLE":
        raise SystemExit("SECTOR_INTELLIGENCE_FAILED: sector source unavailable")

    print("SECTOR_INTELLIGENCE_AVAILABLE")
    print("Classified symbols:", result.get("classified_symbols"))
    print("Unclassified universe symbols:", result.get("unclassified_universe_symbols"))

    ranked = sorted(
        (dict(value, sector=key) for key, value in (result.get("sectors") or {}).items()),
        key=lambda row: row.get("strength", 0),
        reverse=True,
    )
    for row in ranked[:5]:
        print(
            f"STRONGEST {row['sector']}: "
            f"strength={row['strength']} breadth={row['breadth_pct']}% "
            f"avg={row['average_change_pct']}"
        )
    for row in ranked[-5:]:
        print(
            f"WEAKEST {row['sector']}: "
            f"strength={row['strength']} breadth={row['breadth_pct']}% "
            f"avg={row['average_change_pct']}"
        )


if __name__ == "__main__":
    main()
