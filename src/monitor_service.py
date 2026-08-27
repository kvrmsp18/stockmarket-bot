"""End-to-end, read-only service for the live intraday stock monitor."""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping, Sequence

from .production_market_data import ProductionMarketDataProvider
from .research_fundamentals import FundamentalConfig
from .research_pipeline import ResearchPipelineConfig, ResearchScanResult, scan_symbols
from .research_market_data import ResearchMarketDataProvider
from .risk_management import RiskConfig
from .stock_monitor import StockMonitorSnapshot, build_monitor_snapshot

# No fixed small stock list. The research pipeline is responsible for resolving
# the complete configured market universe when no explicit symbols are passed.
BOT_RESEARCH_UNIVERSE: tuple[str, ...] = ()
LIVE_MONITOR_PERIOD = "5d"
LIVE_MONITOR_INTERVAL = "5m"


def run_stock_monitor(
    symbols: Sequence[str] | None = None,
    *,
    account_equity: float = 1000.0,
    provider: ResearchMarketDataProvider | None = None,
    research_config: ResearchPipelineConfig | None = None,
    risk_config: RiskConfig | None = None,
    selected_quantities: Mapping[str, int] | None = None,
) -> tuple[ResearchScanResult, StockMonitorSnapshot]:
    """Run one complete read-only intraday scan."""
    # Deliberately do not substitute a 26/29-stock hard-coded list. The
    # research pipeline resolves the complete market universe when symbols is
    # omitted; explicit symbols are retained only for tests/tooling.
    universe = tuple(symbols) if symbols else None
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
        candidate_config=replace(cfg.candidate_config, require_mtf_rsi_confirmation=False),
    )
    data_provider = provider or ProductionMarketDataProvider(timeout=12.0)
    scan = scan_symbols(universe, provider=data_provider, config=cfg)
    snapshot = build_monitor_snapshot(
        scan, account_equity, config=cfg.risk_config, selected_quantities=selected_quantities
    )
    return scan, snapshot
