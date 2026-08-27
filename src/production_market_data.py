"""Production market-data policy for the NSE/BSE research application.

Order of preference for intraday research:
1. DhanHQ read-only intraday OHLCV when credentials are configured.
2. Twelve Data read-only intraday OHLCV when the configured Dhan feed fails.
3. Official NSE/BSE historical data for daily/EOD research only.

For daily/EOD analysis, official NSE/BSE remains authoritative. No broker
order API is used by this module.
"""

from __future__ import annotations

from .research_market_data import (
    OfficialBSEMarketDataProvider,
    OfficialNSEMarketDataProvider,
    ResearchBar,
    ResearchMarketDataError,
    ResilientMarketDataProvider,
)
from .twelve_data_market_data import TwelveDataMarketDataProvider


class ProductionMarketDataProvider:
    """Dhan intraday first, Twelve Data second, official exchange for daily/EOD."""

    name = "production_market_data"

    def __init__(self, timeout: float = 12.0) -> None:
        self.timeout = timeout
        self.primary = ResilientMarketDataProvider(timeout=timeout)
        self.twelve_data = TwelveDataMarketDataProvider(timeout=timeout)
        self.nse = OfficialNSEMarketDataProvider(timeout=timeout)
        self.bse = OfficialBSEMarketDataProvider(timeout=timeout)
        self.last_source: str | None = None
        self.last_errors: tuple[str, ...] = ()

    @property
    def available_sources(self) -> tuple[str, ...]:
        sources = ["official_nse", "official_bse"]
        if self.twelve_data.configured:
            sources.insert(0, "twelvedata_intraday")
        if getattr(self.primary, "dhan", None) is not None and self.primary.dhan.configured:
            sources.insert(0, "dhanhq_intraday")
        return tuple(sources)

    def history(
        self,
        symbol: str,
        *,
        period: str = "5d",
        interval: str = "15m",
    ) -> tuple[ResearchBar, ...]:
        normalized = symbol.strip().upper()
        errors: list[str] = []
        daily_interval = interval.strip().lower() in {"1d", "1day", "day", "daily"}

        # Dhan is the preferred live intraday feed when configured.
        try:
            bars = self.primary.history(normalized, period=period, interval=interval)
            self.last_source = self.primary.last_source or "dhanhq_intraday"
            self.last_errors = tuple(getattr(self.primary, "last_errors", ()))
            return bars
        except ResearchMarketDataError as exc:
            errors.append(f"dhan: {exc}")

        # Dhan authentication can fail independently of the research engine.
        # Do not turn that broker credential failure into a total data outage
        # when the configured read-only intraday provider can supply the bars.
        if not daily_interval and self.twelve_data.configured:
            try:
                bars = self.twelve_data.history(
                    normalized,
                    period=period,
                    interval=interval,
                )
                self.last_source = self.twelve_data.name
                self.last_errors = tuple(errors)
                return bars
            except ResearchMarketDataError as exc:
                errors.append(f"{self.twelve_data.name}: {exc}")

        # IMPORTANT SAFETY RULE:
        # Never return daily exchange candles for an intraday request. Doing so
        # would let the research engine analyse daily bars as if they were 15m
        # bars and could produce misleading intraday candidates.
        if not daily_interval:
            self.last_source = None
            self.last_errors = tuple(errors)
            raise ResearchMarketDataError(
                f"No usable intraday market data for {normalized}: {'; '.join(errors)}"
            )

        provider = (
            self.nse
            if normalized.endswith(".NS")
            else self.bse
            if normalized.endswith(".BO")
            else None
        )
        if provider is None:
            self.last_source = None
            self.last_errors = tuple(errors)
            raise ResearchMarketDataError(
                f"No official exchange provider for {normalized}: {'; '.join(errors)}"
            )

        try:
            bars = provider.history(
                normalized,
                period=period,
                interval="1d",
            )
            self.last_source = f"{provider.name}_daily"
            self.last_errors = tuple(errors)
            return bars
        except ResearchMarketDataError as exc:
            errors.append(f"{provider.name}: {exc}")
            self.last_source = None
            self.last_errors = tuple(errors)
            raise ResearchMarketDataError(
                f"No usable daily/EOD market data for {normalized}: {'; '.join(errors)}"
            ) from exc


ResilientProductionMarketDataProvider = ProductionMarketDataProvider
