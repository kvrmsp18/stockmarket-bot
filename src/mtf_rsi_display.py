"""Display-only multi-timeframe RSI enrichment for suggested stocks."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .chart_history import ChartTimeframe, DhanChartProvider
from .research_market_data import ResearchBar, ResearchMarketDataProvider
from .technical_analysis import MultiTimeframeRSI, TechnicalAnalysisError, _rsi, analyze_multi_timeframe_rsi

MTF_RSI_HISTORY_DAYS = 900


@dataclass(frozen=True)
class RSIHistoryPoint:
    timestamp: object
    value: float


@dataclass(frozen=True)
class DisplayMTFRSIHistory:
    snapshot: MultiTimeframeRSI
    daily: tuple[RSIHistoryPoint, ...]
    weekly: tuple[RSIHistoryPoint, ...]
    monthly: tuple[RSIHistoryPoint, ...]


def _market_symbol(symbol: str, exchange: str) -> str:
    """Normalize a dashboard symbol to exactly one NSE/BSE market suffix."""
    normalized = symbol.strip().upper()
    if normalized.endswith((".NS", ".BO")):
        return normalized
    return f"{normalized}.NS" if exchange.strip().upper() == "NSE" else f"{normalized}.BO"


def _aggregate_period_bars(daily_bars: Sequence[ResearchBar], *, timeframe: str) -> tuple[ResearchBar, ...]:
    if timeframe not in {"weekly", "monthly"}:
        raise ValueError("timeframe must be 'weekly' or 'monthly'.")
    buckets: dict[tuple[int, int], list[ResearchBar]] = {}
    for bar in sorted(daily_bars, key=lambda item: item.timestamp):
        if timeframe == "weekly":
            iso = bar.timestamp.isocalendar()
            key = (iso.year, iso.week)
        else:
            key = (bar.timestamp.year, bar.timestamp.month)
        buckets.setdefault(key, []).append(bar)
    aggregated: list[ResearchBar] = []
    for bucket in buckets.values():
        ordered = sorted(bucket, key=lambda item: item.timestamp)
        first, last = ordered[0], ordered[-1]
        aggregated.append(ResearchBar(
            symbol=last.symbol,
            timestamp=last.timestamp,
            open=first.open,
            high=max(item.high for item in ordered),
            low=min(item.low for item in ordered),
            close=last.close,
            volume=sum(item.volume for item in ordered),
        ))
    return tuple(sorted(aggregated, key=lambda item: item.timestamp))


def _rsi_series(bars: Sequence[ResearchBar], period: int = 14) -> tuple[RSIHistoryPoint, ...]:
    ordered = tuple(sorted(bars, key=lambda item: item.timestamp))
    if len(ordered) < period + 1:
        return ()
    points: list[RSIHistoryPoint] = []
    closes = [bar.close for bar in ordered]
    for end in range(period + 1, len(closes) + 1):
        value = _rsi(closes[:end], period)
        if value is not None:
            points.append(RSIHistoryPoint(ordered[end - 1].timestamp, float(value)))
    return tuple(points)


def _load_daily_history(symbol: str, exchange: str) -> tuple[ResearchBar, ...]:
    market_symbol = _market_symbol(symbol, exchange)
    timeframe = ChartTimeframe("RSI900D", "RSI900D", MTF_RSI_HISTORY_DAYS, "1D", False)
    return DhanChartProvider().history(market_symbol, timeframe)


def calculate_display_mtf_rsi_history(symbol: str, exchange: str, provider: ResearchMarketDataProvider | None = None) -> DisplayMTFRSIHistory:
    """Load enough daily history to calculate M/W/D RSI and graph the series.

    Dhan Data APIs are used first because the dashboard has the paid read-only
    Data API enabled. The optional legacy provider remains available only as a
    controlled fallback when Dhan historical data cannot be read.
    """
    try:
        daily_bars = _load_daily_history(symbol, exchange)
    except Exception as dhan_exc:
        if provider is None:
            raise TechnicalAnalysisError(str(dhan_exc)) from dhan_exc
        market_symbol = _market_symbol(symbol, exchange)
        try:
            daily_bars = tuple(provider.history(market_symbol, period="2y", interval="1d"))
        except Exception as fallback_exc:
            raise TechnicalAnalysisError(f"Dhan historical data failed: {dhan_exc}; fallback failed: {fallback_exc}") from fallback_exc

    if not daily_bars:
        raise TechnicalAnalysisError(f"No daily history available for {symbol}.")
    weekly_bars = _aggregate_period_bars(daily_bars, timeframe="weekly")
    monthly_bars = _aggregate_period_bars(daily_bars, timeframe="monthly")
    snapshot = analyze_multi_timeframe_rsi(monthly_bars, weekly_bars, daily_bars)
    return DisplayMTFRSIHistory(
        snapshot=snapshot,
        daily=_rsi_series(daily_bars),
        weekly=_rsi_series(weekly_bars),
        monthly=_rsi_series(monthly_bars),
    )


def calculate_display_mtf_rsi(symbol: str, exchange: str, provider: ResearchMarketDataProvider) -> MultiTimeframeRSI:
    """Return the latest monthly/weekly/daily RSI(14) snapshot."""
    return calculate_display_mtf_rsi_history(symbol, exchange, provider).snapshot
