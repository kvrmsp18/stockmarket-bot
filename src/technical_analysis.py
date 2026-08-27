"""Provider-neutral technical analysis for read-only market research.

The module converts validated OHLCV bars into deterministic features used by the
research/algorithmic screening engine.  It never places broker orders.

The analysis intentionally separates:
- trend: SMA20/SMA50 and EMA20
- momentum: RSI14 and 5-bar momentum
- volatility: ATR14
- participation: volume SMA20, volume ratio and directional volume
- execution context: VWAP, candle body and close-location
- algorithmic confirmation: MA alignment, EMA slope and entry confirmation

Multi-timeframe RSI remains a regime-confirmation layer and cannot independently
create a trade.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .research_market_data import ResearchBar


class TechnicalAnalysisError(ValueError):
    """Raised when there is insufficient or invalid market history."""


@dataclass(frozen=True)
class TechnicalSnapshot:
    symbol: str
    timestamp: object
    close: float
    sma_20: float | None
    sma_50: float | None
    ema_20: float | None
    rsi_14: float | None
    atr_14: float | None
    volume_sma_20: float | None
    volume_ratio: float | None
    momentum_5: float | None
    trend: str
    ema20_slope_pct: float | None = None
    price_vs_ema20_pct: float | None = None
    ema20_vs_sma20_pct: float | None = None
    sma20_vs_sma50_pct: float | None = None
    volume_strength: str = "UNKNOWN"
    volume_confirmed: bool = False
    algo_trend_score: float = 0.0
    vwap: float | None = None
    price_vs_vwap_pct: float | None = None
    candle_body_pct: float | None = None
    close_location: float | None = None
    directional_volume_score: float | None = None
    volume_direction: str = "UNKNOWN"
    entry_confirmation_score: float = 0.0
    entry_confirmation: str = "UNAVAILABLE"


@dataclass(frozen=True)
class MultiTimeframeRSI:
    """RSI(14) alignment across monthly, weekly and daily bars."""

    symbol: str
    monthly_rsi_14: float | None
    weekly_rsi_14: float | None
    daily_rsi_14: float | None
    alignment: str
    all_three_agree: bool
    all_three_overbought: bool


def _validate_bars(bars: Sequence[ResearchBar], *, label: str) -> None:
    if not bars:
        raise TechnicalAnalysisError(f"{label} history cannot be empty.")
    if any(
        bar.close <= 0
        or bar.high < bar.low
        or bar.open <= 0
        or bar.volume < 0
        or bar.open < bar.low
        or bar.open > bar.high
        or bar.close < bar.low
        or bar.close > bar.high
        for bar in bars
    ):
        raise TechnicalAnalysisError(f"Invalid OHLCV data supplied for {label}.")


def _sma(values: Sequence[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _ema(values: Sequence[float], period: int) -> float | None:
    if len(values) < period:
        return None
    multiplier = 2.0 / (period + 1)
    ema = sum(values[:period]) / period
    for value in values[period:]:
        ema = (value - ema) * multiplier + ema
    return ema


def _rsi(values: Sequence[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    relative_strength = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def _atr(bars: Sequence[ResearchBar], period: int = 14) -> float | None:
    if len(bars) < period + 1:
        return None
    true_ranges: list[float] = []
    for previous, current in zip(bars[:-1], bars[1:]):
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return sum(true_ranges[-period:]) / period


def _pct_difference(left: float | None, right: float | None) -> float | None:
    if left is None or right is None or right == 0:
        return None
    return ((left / right) - 1.0) * 100.0


def _ema_slope_pct(closes: Sequence[float], period: int = 20) -> float | None:
    if len(closes) < period + 1:
        return None
    current = _ema(closes, period)
    previous = _ema(closes[:-1], period)
    return _pct_difference(current, previous)


def _volume_strength(volume_ratio: float | None) -> str:
    if volume_ratio is None:
        return "UNKNOWN"
    if volume_ratio < 0.80:
        return "VERY_WEAK"
    if volume_ratio < 1.00:
        return "WEAK"
    if volume_ratio < 1.20:
        return "NORMAL"
    if volume_ratio < 1.50:
        return "GOOD"
    if volume_ratio < 2.00:
        return "STRONG"
    return "VERY_STRONG"


def _session_vwap(bars: Sequence[ResearchBar]) -> float | None:
    """Return VWAP for the latest trading date when multiple bars exist."""
    if len(bars) < 2:
        return None
    latest_date = bars[-1].timestamp.date()
    session = [bar for bar in bars if bar.timestamp.date() == latest_date]
    if len(session) < 2:
        return None
    value_volume = sum(((bar.high + bar.low + bar.close) / 3.0) * bar.volume for bar in session)
    total_volume = sum(bar.volume for bar in session)
    if total_volume <= 0:
        return None
    return value_volume / total_volume


def _directional_volume(bars: Sequence[ResearchBar], volume_ratio: float | None) -> tuple[float | None, str]:
    """Estimate whether high participation is aligned with buying or selling."""
    if not bars or volume_ratio is None:
        return None, "UNKNOWN"
    bar = bars[-1]
    spread = max(bar.high - bar.low, 1e-9)
    body = (bar.close - bar.open) / spread
    location = ((bar.close - bar.low) / spread) * 2.0 - 1.0
    raw = max(-1.0, min(1.0, 0.65 * body + 0.35 * location))
    scaled = raw * min(max(volume_ratio / 1.20, 0.0), 1.5) / 1.5
    if scaled >= 0.20:
        direction = "BUYING"
    elif scaled <= -0.20:
        direction = "SELLING"
    else:
        direction = "NEUTRAL"
    return round(scaled, 3), direction


def _entry_confirmation(bars: Sequence[ResearchBar], trend: str, vwap: float | None) -> tuple[float, str]:
    """Score a real entry trigger instead of treating a trend as an entry."""
    if len(bars) < 2:
        return 0.0, "UNAVAILABLE"
    current = bars[-1]
    previous = bars[-2]
    spread = max(current.high - current.low, 1e-9)
    close_location = (current.close - current.low) / spread
    score = 0.0

    if trend == "BULLISH":
        if current.close > previous.high:
            score += 0.55
        if current.close > current.open:
            score += 0.20
        if close_location >= 0.70:
            score += 0.15
        if vwap is not None:
            score += 0.10 if current.close > vwap else -0.10
        return round(max(0.0, min(1.0, score)), 2), "BUY_TRIGGER" if score >= 0.60 else "BUY_SETUP"

    if trend == "BEARISH":
        if current.close < previous.low:
            score += 0.55
        if current.close < current.open:
            score += 0.20
        if close_location <= 0.30:
            score += 0.15
        if vwap is not None:
            score += 0.10 if current.close < vwap else -0.10
        return round(max(0.0, min(1.0, score)), 2), "SELL_TRIGGER" if score >= 0.60 else "SELL_SETUP"

    return 0.0, "NO_TREND"


def _algo_trend_score(*, close: float, ema20: float | None, sma20: float | None, sma50: float | None, ema_slope: float | None, trend: str) -> float:
    bullish_points = 0.0
    bearish_points = 0.0
    if ema20 is not None:
        if close > ema20:
            bullish_points += 0.25
        elif close < ema20:
            bearish_points += 0.25
    if ema20 is not None and sma20 is not None:
        if ema20 > sma20:
            bullish_points += 0.25
        elif ema20 < sma20:
            bearish_points += 0.25
    if sma20 is not None and sma50 is not None:
        if sma20 > sma50:
            bullish_points += 0.25
        elif sma20 < sma50:
            bearish_points += 0.25
    if ema_slope is not None:
        if ema_slope > 0:
            bullish_points += 0.25
        elif ema_slope < 0:
            bearish_points += 0.25
    if trend == "BULLISH":
        return round(bullish_points, 2)
    if trend == "BEARISH":
        return round(bearish_points, 2)
    return round(max(bullish_points, bearish_points) * 0.5, 2)


def analyze(bars: Sequence[ResearchBar]) -> TechnicalSnapshot:
    """Calculate a deterministic technical snapshot from chronological bars."""
    _validate_bars(bars, label="technical")
    ordered = tuple(sorted(bars, key=lambda bar: bar.timestamp))
    closes = [bar.close for bar in ordered]
    volumes = [float(bar.volume) for bar in ordered]
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    ema20 = _ema(closes, 20)
    rsi14 = _rsi(closes, 14)
    atr14 = _atr(ordered, 14)
    volume_sma20 = _sma(volumes, 20)
    volume_ratio = volumes[-1] / volume_sma20 if volume_sma20 is not None and volume_sma20 > 0 else None
    momentum5 = ((closes[-1] / closes[-6]) - 1.0) * 100.0 if len(closes) >= 6 else None

    if sma20 is not None and sma50 is not None:
        if closes[-1] > sma20 > sma50:
            trend = "BULLISH"
        elif closes[-1] < sma20 < sma50:
            trend = "BEARISH"
        else:
            trend = "MIXED"
    elif sma20 is not None:
        trend = "BULLISH" if closes[-1] > sma20 else "BEARISH"
    else:
        trend = "INSUFFICIENT_HISTORY"

    ema_slope = _ema_slope_pct(closes, 20)
    price_vs_ema = _pct_difference(closes[-1], ema20)
    ema_vs_sma = _pct_difference(ema20, sma20)
    sma_spread = _pct_difference(sma20, sma50)
    volume_strength = _volume_strength(volume_ratio)
    volume_confirmed = volume_ratio is not None and volume_ratio >= 1.0
    algo_trend_score = _algo_trend_score(close=closes[-1], ema20=ema20, sma20=sma20, sma50=sma50, ema_slope=ema_slope, trend=trend)
    vwap = _session_vwap(ordered)
    price_vs_vwap = _pct_difference(closes[-1], vwap)
    latest = ordered[-1]
    spread = max(latest.high - latest.low, 1e-9)
    candle_body_pct = ((latest.close - latest.open) / latest.open) * 100.0
    close_location = (latest.close - latest.low) / spread
    directional_volume_score, volume_direction = _directional_volume(ordered, volume_ratio)
    entry_confirmation_score, entry_confirmation = _entry_confirmation(ordered, trend, vwap)

    return TechnicalSnapshot(
        symbol=latest.symbol, timestamp=latest.timestamp, close=latest.close,
        sma_20=sma20, sma_50=sma50, ema_20=ema20, rsi_14=rsi14, atr_14=atr14,
        volume_sma_20=volume_sma20, volume_ratio=volume_ratio, momentum_5=momentum5,
        trend=trend, ema20_slope_pct=ema_slope, price_vs_ema20_pct=price_vs_ema,
        ema20_vs_sma20_pct=ema_vs_sma, sma20_vs_sma50_pct=sma_spread,
        volume_strength=volume_strength, volume_confirmed=volume_confirmed,
        algo_trend_score=algo_trend_score, vwap=vwap, price_vs_vwap_pct=price_vs_vwap,
        candle_body_pct=candle_body_pct, close_location=close_location,
        directional_volume_score=directional_volume_score, volume_direction=volume_direction,
        entry_confirmation_score=entry_confirmation_score, entry_confirmation=entry_confirmation,
    )


def analyze_multi_timeframe_rsi(monthly_bars: Sequence[ResearchBar], weekly_bars: Sequence[ResearchBar], daily_bars: Sequence[ResearchBar]) -> MultiTimeframeRSI:
    """Calculate RSI(14) alignment across monthly, weekly and daily history."""
    _validate_bars(monthly_bars, label="monthly")
    _validate_bars(weekly_bars, label="weekly")
    _validate_bars(daily_bars, label="daily")
    symbols = {monthly_bars[-1].symbol, weekly_bars[-1].symbol, daily_bars[-1].symbol}
    if len(symbols) != 1:
        raise TechnicalAnalysisError("Monthly, weekly and daily symbols must match.")
    monthly_rsi = _rsi([bar.close for bar in monthly_bars], 14)
    weekly_rsi = _rsi([bar.close for bar in weekly_bars], 14)
    daily_rsi = _rsi([bar.close for bar in daily_bars], 14)
    values = (monthly_rsi, weekly_rsi, daily_rsi)
    if any(value is None for value in values):
        alignment = "INSUFFICIENT_HISTORY"
        all_three_agree = False
        all_three_overbought = False
    else:
        momentum_sides = tuple("BULLISH" if value >= 50.0 else "BEARISH" for value in values)
        all_three_agree = len(set(momentum_sides)) == 1
        alignment = momentum_sides[0] if all_three_agree else "MIXED"
        all_three_overbought = all(value >= 70.0 for value in values)
    return MultiTimeframeRSI(
        symbol=monthly_bars[-1].symbol,
        monthly_rsi_14=monthly_rsi,
        weekly_rsi_14=weekly_rsi,
        daily_rsi_14=daily_rsi,
        alignment=alignment,
        all_three_agree=all_three_agree,
        all_three_overbought=all_three_overbought,
    )
