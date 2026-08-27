"""Advanced deterministic technical indicators for the intraday research engine.

This module uses only OHLCV bars already supplied to the research pipeline. It
never places orders. The institutional score is an explicitly labelled
participation proxy, not a claim of observing institutional orders.

Core confirmation indicators:
MACD, Bollinger Bands, CCI, MFI, Stochastic, Vortex, Ichimoku, Supertrend,
Parabolic SAR, OBV, Accumulation/Distribution, Pivot Points, Fibonacci context,
RVOL, ATR volatility, support/resistance, and a multi-indicator directional
score. Elliott Wave, Gann and time-cycle studies are deliberately not hard
trade gates because their interpretation is subjective.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .research_market_data import ResearchBar


@dataclass(frozen=True)
class AdvancedTechnicalSnapshot:
    macd: float | None
    macd_signal: float | None
    macd_histogram: float | None
    bollinger_middle: float | None
    bollinger_upper: float | None
    bollinger_lower: float | None
    bollinger_position: float | None
    cci_20: float | None
    mfi_14: float | None
    stochastic_k: float | None
    stochastic_d: float | None
    vortex_plus: float | None
    vortex_minus: float | None
    ichimoku_tenkan: float | None
    ichimoku_kijun: float | None
    ichimoku_span_a: float | None
    ichimoku_span_b: float | None
    supertrend: float | None
    supertrend_direction: str
    parabolic_sar: float | None
    obv: float | None
    obv_slope: float | None
    accumulation_distribution: float | None
    ad_slope: float | None
    rvol: float | None
    volatility_pct: float | None
    pivot: float | None
    pivot_r1: float | None
    pivot_s1: float | None
    pivot_r2: float | None
    pivot_s2: float | None
    fib_382: float | None
    fib_500: float | None
    fib_618: float | None
    support: float | None
    resistance: float | None
    institutional_participation_score: float
    bullish_score: float
    bearish_score: float
    confirmation_score: float
    direction: str
    regime: str
    warnings: tuple[str, ...]


def _sma(v: Sequence[float], p: int) -> float | None:
    return sum(v[-p:]) / p if len(v) >= p else None


def _ema(v: Sequence[float], p: int) -> float | None:
    if len(v) < p:
        return None
    a = 2.0 / (p + 1.0)
    out = sum(v[:p]) / p
    for x in v[p:]:
        out += a * (x - out)
    return out


def _std(v: Sequence[float], p: int) -> float | None:
    if len(v) < p:
        return None
    m = sum(v[-p:]) / p
    return (sum((x - m) ** 2 for x in v[-p:]) / p) ** 0.5


def _atr(bars: Sequence[ResearchBar], p: int = 14) -> float | None:
    if len(bars) < p + 1:
        return None
    trs = []
    for prev, cur in zip(bars[:-1], bars[1:]):
        trs.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
    return sum(trs[-p:]) / p


def _macd(v: Sequence[float]) -> tuple[float | None, float | None, float | None]:
    if len(v) < 35:
        return None, None, None
    series: list[float] = []
    for i in range(26, len(v) + 1):
        fast = _ema(v[:i], 12)
        slow = _ema(v[:i], 26)
        if fast is not None and slow is not None:
            series.append(fast - slow)
    if len(series) < 9:
        return None, None, None
    signal = _ema(series, 9)
    return series[-1], signal, series[-1] - signal if signal is not None else None


def _bollinger(v: Sequence[float], p: int = 20, k: float = 2.0):
    middle = _sma(v, p)
    deviation = _std(v, p)
    if middle is None or deviation is None:
        return None, None, None, None
    upper, lower = middle + k * deviation, middle - k * deviation
    position = (v[-1] - lower) / (upper - lower) if upper != lower else 0.5
    return middle, upper, lower, max(0.0, min(1.0, position))


def _cci(bars: Sequence[ResearchBar], p: int = 20) -> float | None:
    if len(bars) < p:
        return None
    tp = [(b.high + b.low + b.close) / 3.0 for b in bars]
    mean = sum(tp[-p:]) / p
    deviation = sum(abs(x - mean) for x in tp[-p:]) / p
    return (tp[-1] - mean) / (0.015 * deviation) if deviation else 0.0


def _mfi(bars: Sequence[ResearchBar], p: int = 14) -> float | None:
    if len(bars) < p + 1:
        return None
    tp = [(b.high + b.low + b.close) / 3.0 for b in bars]
    positive = negative = 0.0
    for i in range(-p, 0):
        flow = tp[i] * max(float(bars[i].volume), 0.0)
        if tp[i] > tp[i - 1]:
            positive += flow
        elif tp[i] < tp[i - 1]:
            negative += flow
    if negative == 0:
        return 100.0 if positive else 50.0
    return 100.0 - 100.0 / (1.0 + positive / negative)


def _stochastic(bars: Sequence[ResearchBar], p: int = 14, d: int = 3):
    if len(bars) < p:
        return None, None
    ks = []
    for i in range(p - 1, len(bars)):
        window = bars[i - p + 1 : i + 1]
        high, low = max(b.high for b in window), min(b.low for b in window)
        ks.append(100.0 * (bars[i].close - low) / (high - low) if high != low else 50.0)
    return ks[-1], sum(ks[-d:]) / d if len(ks) >= d else None


def _vortex(bars: Sequence[ResearchBar], p: int = 14):
    if len(bars) < p + 1:
        return None, None
    tr, vp, vm = [], [], []
    for prev, cur in zip(bars[:-1], bars[1:]):
        tr.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
        vp.append(abs(cur.high - prev.low))
        vm.append(abs(cur.low - prev.high))
    total = sum(tr[-p:])
    return (sum(vp[-p:]) / total, sum(vm[-p:]) / total) if total else (None, None)


def _ichimoku(bars: Sequence[ResearchBar]):
    if len(bars) < 52:
        return (None,) * 4
    def mid(window):
        return (max(b.high for b in window) + min(b.low for b in window)) / 2.0
    tenkan, kijun = mid(bars[-9:]), mid(bars[-26:])
    return tenkan, kijun, (tenkan + kijun) / 2.0, mid(bars[-52:])


def _supertrend(bars: Sequence[ResearchBar], p: int = 10, multiplier: float = 3.0):
    if len(bars) < p + 2:
        return None, "UNKNOWN"
    atr = _atr(bars, p)
    if atr is None:
        return None, "UNKNOWN"
    midpoint = (bars[-1].high + bars[-1].low) / 2.0
    upper, lower, close = midpoint + multiplier * atr, midpoint - multiplier * atr, bars[-1].close
    return (lower, "BULLISH") if close >= lower else (upper, "BEARISH")


def _parabolic_sar(bars: Sequence[ResearchBar]):
    if len(bars) < 5:
        return None
    up = bars[-1].close >= bars[-5].close
    return min(b.low for b in bars[-5:]) if up else max(b.high for b in bars[-5:])


def _obv(bars: Sequence[ResearchBar]):
    if not bars:
        return None, None
    values = [0.0]
    for prev, cur in zip(bars[:-1], bars[1:]):
        if cur.close > prev.close:
            values.append(values[-1] + float(cur.volume))
        elif cur.close < prev.close:
            values.append(values[-1] - float(cur.volume))
        else:
            values.append(values[-1])
    return values[-1], values[-1] - values[-6] if len(values) >= 6 else None


def _ad(bars: Sequence[ResearchBar]):
    if not bars:
        return None, None
    value, values = 0.0, []
    for b in bars:
        spread = b.high - b.low
        mfm = ((b.close - b.low) - (b.high - b.close)) / spread if spread else 0.0
        value += mfm * float(b.volume)
        values.append(value)
    return value, values[-1] - values[-6] if len(values) >= 6 else None


def _pivots(bars: Sequence[ResearchBar]):
    if len(bars) < 2:
        return (None,) * 5
    b = bars[-2]
    p = (b.high + b.low + b.close) / 3.0
    return p, 2 * p - b.low, 2 * p - b.high, p + (b.high - b.low), p - (b.high - b.low)


def _fib(bars: Sequence[ResearchBar]):
    if len(bars) < 20:
        return (None,) * 3
    window = bars[-60:] if len(bars) >= 60 else bars[-20:]
    high, low = max(b.high for b in window), min(b.low for b in window)
    span = high - low
    return high - 0.382 * span, high - 0.5 * span, high - 0.618 * span


def _support_resistance(bars: Sequence[ResearchBar]):
    if len(bars) < 20:
        return None, None
    window = bars[-20:]
    return min(b.low for b in window), max(b.high for b in window)


def _score(
    bars, macd_hist, bb_pos, cci, mfi, stoch_k, stoch_d, vp, vm,
    tenkan, kijun, span_a, span_b, st_dir, psar, obv_slope, ad_slope,
    rvol, pivot, support, resistance,
):
    close = bars[-1].close
    bullish = bearish = 0.0
    checks = 0.0

    def add(condition: bool | None, weight: float = 1.0):
        nonlocal bullish, bearish, checks
        if condition is None:
            return
        checks += weight
        if condition:
            bullish += weight
        else:
            bearish += weight

    add(None if macd_hist is None else macd_hist >= 0, 1.4)
    add(None if bb_pos is None else bb_pos >= 0.50, 0.8)
    add(None if cci is None else cci >= 0, 0.8)
    add(None if mfi is None else mfi >= 50, 0.8)
    add(None if stoch_k is None or stoch_d is None else stoch_k >= stoch_d, 0.8)
    add(None if vp is None or vm is None else vp >= vm, 0.9)
    add(None if tenkan is None or kijun is None else tenkan >= kijun, 1.0)
    add(None if span_a is None or span_b is None else close >= max(span_a, span_b), 1.0)
    add(None if st_dir == "UNKNOWN" else st_dir == "BULLISH", 1.2)
    add(None if psar is None else close >= psar, 0.8)
    add(None if obv_slope is None else obv_slope >= 0, 1.0)
    add(None if ad_slope is None else ad_slope >= 0, 0.9)
    add(None if rvol is None else rvol >= 1.0, 1.1)
    add(None if pivot is None else close >= pivot, 0.8)
    add(None if support is None else close > support, 0.6)
    add(None if resistance is None else close < resistance * 0.995, 0.4)
    if checks <= 0:
        return 0.5, 0.5, 0.5, "MIXED"
    bull, bear = bullish / checks, bearish / checks
    direction = "BULLISH" if bull - bear >= 0.08 else "BEARISH" if bear - bull >= 0.08 else "MIXED"
    return bull, bear, max(bull, bear), direction


def analyze_advanced(bars: Sequence[ResearchBar]) -> AdvancedTechnicalSnapshot:
    if not bars:
        raise ValueError("Advanced technical history cannot be empty.")
    ordered = tuple(sorted(bars, key=lambda b: b.timestamp))
    close = ordered[-1].close
    closes = [b.close for b in ordered]
    volumes = [float(b.volume) for b in ordered]
    macd, macd_signal, macd_hist = _macd(closes)
    bb_mid, bb_up, bb_low, bb_pos = _bollinger(closes)
    cci, mfi = _cci(ordered), _mfi(ordered)
    stoch_k, stoch_d = _stochastic(ordered)
    vp, vm = _vortex(ordered)
    tenkan, kijun, span_a, span_b = _ichimoku(ordered)
    supertrend, supertrend_direction = _supertrend(ordered)
    psar = _parabolic_sar(ordered)
    obv, obv_slope = _obv(ordered)
    ad, ad_slope = _ad(ordered)
    atr = _atr(ordered)
    volume_sma = _sma(volumes, 20)
    rvol = volumes[-1] / volume_sma if volume_sma and volume_sma > 0 else None
    volatility_pct = atr / close * 100.0 if atr and close > 0 else None
    pivot, r1, s1, r2, s2 = _pivots(ordered)
    fib_382, fib_500, fib_618 = _fib(ordered)
    support, resistance = _support_resistance(ordered)
    bull, bear, confirmation, direction = _score(
        ordered, macd_hist, bb_pos, cci, mfi, stoch_k, stoch_d, vp, vm,
        tenkan, kijun, span_a, span_b, supertrend_direction, psar, obv_slope,
        ad_slope, rvol, pivot, support, resistance,
    )

    participation = []
    if rvol is not None:
        participation.append(1.0 if rvol >= 1.5 else 0.0)
    if obv_slope is not None:
        participation.append(1.0 if obv_slope > 0 else 0.0)
    if ad_slope is not None:
        participation.append(1.0 if ad_slope > 0 else 0.0)
    if pivot is not None:
        participation.append(1.0 if close >= pivot else 0.0)
    institutional_proxy = sum(participation) / len(participation) if participation else 0.5

    warnings: list[str] = []
    if rvol is not None and rvol >= 3.0:
        warnings.append("EXTREME_RVOL")
    elif rvol is not None and rvol >= 2.0:
        warnings.append("HIGH_RVOL")
    if bb_pos is not None and (bb_pos >= 0.95 or bb_pos <= 0.05):
        warnings.append("BOLLINGER_EXTREME")
    if volatility_pct is not None and volatility_pct >= 3.0:
        warnings.append("HIGH_ATR_VOLATILITY")
    if support is not None and resistance is not None and resistance > support:
        if (resistance - support) / max(close, 1e-9) < 0.01:
            warnings.append("TIGHT_RANGE")

    regime = "TRENDING_BULLISH" if direction == "BULLISH" else "TRENDING_BEARISH" if direction == "BEARISH" else "MIXED"
    return AdvancedTechnicalSnapshot(
        macd=macd, macd_signal=macd_signal, macd_histogram=macd_hist,
        bollinger_middle=bb_mid, bollinger_upper=bb_up, bollinger_lower=bb_low,
        bollinger_position=bb_pos, cci_20=cci, mfi_14=mfi, stochastic_k=stoch_k,
        stochastic_d=stoch_d, vortex_plus=vp, vortex_minus=vm,
        ichimoku_tenkan=tenkan, ichimoku_kijun=kijun, ichimoku_span_a=span_a,
        ichimoku_span_b=span_b, supertrend=supertrend,
        supertrend_direction=supertrend_direction, parabolic_sar=psar, obv=obv,
        obv_slope=obv_slope, accumulation_distribution=ad, ad_slope=ad_slope,
        rvol=rvol, volatility_pct=volatility_pct, pivot=pivot, pivot_r1=r1,
        pivot_s1=s1, pivot_r2=r2, pivot_s2=s2, fib_382=fib_382, fib_500=fib_500,
        fib_618=fib_618, support=support, resistance=resistance,
        institutional_participation_score=round(institutional_proxy, 3),
        bullish_score=round(bull, 3), bearish_score=round(bear, 3),
        confirmation_score=round(confirmation, 3), direction=direction,
        regime=regime, warnings=tuple(warnings),
    )
