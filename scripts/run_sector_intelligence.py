from __future__ import annotations

import json
from pathlib import Path

from intraday_bot.brokers import DhanBroker
from intraday_bot.sector_intelligence import build


def main() -> None:
    universe_path = Path("data/universe.json")
    universe = json.loads(universe_path.read_text(encoding="utf-8")) if universe_path.exists() else []
    if not universe:
        raise SystemExit("SECTOR_INTELLIGENCE_FAILED: universe is empty")
    quotes = DhanBroker().bulk_quotes(universe)
    if not quotes:
        raise SystemExit("SECTOR_INTELLIGENCE_FAILED: Dhan returned zero quotes")
    result = build(universe, quotes)
    if result.get("status") != "AVAILABLE":
        raise SystemExit("SECTOR_INTELLIGENCE_FAILED: sector source unavailable")
    print("SECTOR_INTELLIGENCE_AVAILABLE")
    print("Classified symbols:", result.get("classified_symbols"))
    print("Unclassified universe symbols:", result.get("unclassified_universe_symbols"))
    ranked = sorted(
        (dict(v, sector=k) for k, v in (result.get("sectors") or {}).items()),
        key=lambda x: x.get("strength", 0),
        reverse=True,
    )
    for row in ranked[:5]:
        print(f"STRONGEST {row['sector']}: strength={row['strength']} breadth={row['breadth_pct']}% avg={row['average_change_pct']}")
    for row in ranked[-5:]:
        print(f"WEAKEST {row['sector']}: strength={row['strength']} breadth={row['breadth_pct']}% avg={row['average_change_pct']}")


if __name__ == "__main__":
    main()
