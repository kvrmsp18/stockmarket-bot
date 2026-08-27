"""End-to-end, read-only service for the live intraday stock monitor."""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Mapping, Sequence

from .production_market_data import ProductionMarketDataProvider
from .research_fundamentals import FundamentalConfig
from .research_pipeline import ResearchPipelineConfig, ResearchScanResult, scan_symbols
from .research_market_data import ResearchMarketDataProvider
from .risk_management import RiskConfig
from .stock_monitor import StockMonitorSnapshot, build_monitor_snapshot

# The universe is deliberately NOT hard-coded. The GitHub Actions job refreshes
# the official NSE equity universe before each cycle and passes it through this
# environment variable. This prevents the bot from getting stuck on 26/29/etc.
BOT_RESEARCH_UNIVERSE: tuple[str, ...] = ()
LIVE_MONITOR_PERIOD = "5d"
LIVE_MONITOR_INTERVAL = "5m"


def _market_universe_from_environment() -> tuple[str, ...]:
    """Read the complete dynamically refreshed NSE cash-equity universe."""
    raw = os.getenv("BOT_MARKET_UNIVERSE", "").strip()
    if not raw:
        raise RuntimeError(
            "BOT_MARKET_UNIVERSE is empty. The workflow must refresh the official "
            "NSE equity universe before running the monitor."
        )
    symbols: list[str] = []
    seen: set[str] = set()
    for value in raw.split(","):
        symbol = value.strip().upper()
        if symbol and symbol not in seen:
            symbols.append(symbol)
            seen.add(symbol)
    if not symbols:
        raise RuntimeError("The refreshed market universe contains no NSE equity symbols.")
    return tuple(symbols)


def run_stock_monitor(
    symbols: Sequence[str] | None = None,
    *,
    account_equity: float = 1000.0,
    provider: ResearchMarketDataProvider | None = None,
    research_config: ResearchPipelineConfig | None = None,
    risk_config: RiskConfig | None = None,
    selected_quantities: Mapping[str, int] | None = None,
) -> tuple[ResearchScanResult, StockMonitorSnapshot]:
    """Run one complete read-only intraday scan across the dynamic universe."""
    # Explicit symbols are retained for tests/tooling. Production cycles must
    # use the refreshed complete market universe, never a small fixed list.
    universe = tuple(symbols) if symbols is not None else _market_universe_from_environment()

    cfg = research_config or ResearchPipelineConfig(
        period=LIVE_MONITOR_PERIOD,
        interval=LIVE_MONITOR_INTERVAL,
        account_equity=account_equity,
        fundamental_config=FundamentalConfig.from_environment(),
    )
    cfg = replace(
        cfg,
        account_equity=account_equity,
        risk_config=risk_config or cfg.risk_config,
        candidate_config=replace(
            cfg.candidate_config,
            require_mtf_rsi_confirmation=False,
        ),
    )
    data_provider = provider or ProductionMarketDataProvider(timeout=12.0)
    scan = scan_symbols(universe, provider=data_provider, config=cfg)
    snapshot = build_monitor_snapshot(
        scan,
        account_equity,
        config=cfg.risk_config,
        selected_quantities=selected_quantities,
    )
    return scan, snapshot
