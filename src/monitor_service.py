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

BOT_RESEARCH_UNIVERSE: tuple[str, ...] = (
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS",
    "SBIN.NS", "LT.NS", "AXISBANK.NS", "ITC.NS", "BHARTIARTL.NS",
    "KOTAKBANK.NS", "MARUTI.NS", "M&M.NS", "SUNPHARMA.NS", "HINDUNILVR.NS",
    "TMPV.NS", "TMCV.NS", "BAJFINANCE.NS", "ADANIENT.NS", "ADANIPORTS.NS",
    "NTPC.NS", "POWERGRID.NS", "TATASTEEL.NS", "HCLTECH.NS", "WIPRO.NS",
    "TECHM.NS",
)

LIVE_MONITOR_PERIOD = "5d"
LIVE_MONITOR_INTERVAL = "15m"


def run_stock_monitor(
    symbols: Sequence[str] | None = None,
    *,
    account_equity: float = 1000.0,
    provider: ResearchMarketDataProvider | None = None,
    research_config: ResearchPipelineConfig | None = None,
    risk_config: RiskConfig | None = None,
    selected_quantities: Mapping[str, int] | None = None,
) -> tuple[ResearchScanResult, StockMonitorSnapshot]:
    """Run one complete read-only intraday scan using the managed universe.

    Decision chain:
      NIFTY/BANKNIFTY regime -> sector strength -> stock technicals
      -> M/W/D RSI confirmation -> advanced indicators -> fundamentals
      -> risk -> Entry/SL/Target -> ranking.

    The research pipeline is the single source of truth for M/W/D confirmation.
    The older candidate-engine helper also has an MTF check for standalone use,
    but it is disabled here to avoid a second provider call that can reject a
    valid candidate for a provider-specific reason after the real pipeline MTF
    gate has already passed.

    Production data policy is DhanHQ intraday -> Twelve Data intraday ->
    official NSE/BSE daily/EOD fallback. No broker order API is used.
    """
    universe = tuple(symbols) if symbols else BOT_RESEARCH_UNIVERSE
    cfg = research_config or ResearchPipelineConfig(
        period=LIVE_MONITOR_PERIOD,
        interval=LIVE_MONITOR_INTERVAL,
        account_equity=account_equity,
        fundamental_config=FundamentalConfig.from_environment(),
    )

    # The research_pipeline performs the genuine M/W/D gate itself using the
    # configured production provider. Do not run a second, independent MTF
    # provider lookup inside candidate_engine for the live monitor.
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
