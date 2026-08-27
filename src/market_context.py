"""Market-regime and sector-strength context for the read-only research engine.

Decision context:
    NIFTY 50 + BANK NIFTY regime -> sector strength/heatmap -> stock context.

Index regime uses daily OHLCV from the free Yahoo chart adapter because the
Dhan equity chart endpoint is security-master driven and does not reliably
accept index symbols. Equity research itself continues to use the configured
official/Dhan provider chain.

Sector strength is calculated from the already-scanned stock bars. Lookbacks
are timestamp-based so 1D/5D remain correct for both daily and 5-minute bars.
This is a relative-strength proxy, not an exchange-provided sector index feed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Mapping, Sequence

from .research_market_data import ResearchBar, ResearchMarketDataError, YahooFinanceMarketDataProvider


SECTOR_MAP: dict[str, str] = {
    "RELIANCE.NS": "ENERGY", "TCS.NS": "IT", "INFY.NS": "IT", "HCLTECH.NS": "IT", "WIPRO.NS": "IT", "TECHM.NS": "IT",
    "HDFCBANK.NS": "BANKING", "ICICIBANK.NS": "BANKING", "SBIN.NS": "BANKING", "AXISBANK.NS": "BANKING", "KOTAKBANK.NS": "BANKING",
    "BAJFINANCE.NS": "FINANCIALS", "LT.NS": "CAPITAL_GOODS", "ITC.NS": "FMCG", "HINDUNILVR.NS": "FMCG", "BHARTIARTL.NS": "TELECOM",
    "MARUTI.NS": "AUTOMOBILE", "M&M.NS": "AUTOMOBILE", "SUNPHARMA.NS": "PHARMA", "TMPV.NS": "AUTOMOBILE", "TMCV.NS": "AUTOMOBILE",
    "ADANIENT.NS": "METALS_MINING", "ADANIPORTS.NS": "LOGISTICS", "NTPC.NS": "POWER", "POWERGRID.NS": "POWER", "TATASTEEL.NS": "METALS_MINING",
}


@dataclass(frozen=True)
class IndexRegime:
    symbol: str
    label: str
    close: float | None
    change_1d_pct: float | None
    change_5d_pct: float | None
    sma_20: float | None
    sma_50: float | None
    rsi_14: float | None
    regime: str
    score: float
    reason: str


@dataclass(frozen=True)
class SectorStrength:
    sector: str
    stocks: tuple[str, ...]
    breadth_pct: float
    avg_1d_pct: float | None
    avg_5d_pct: float | None
    score: float
    regime: str


@dataclass(frozen=True)
class MarketContext:
    nifty: IndexRegime
    banknifty: IndexRegime
    combined_regime: str
    combined_score: float
    sector_strength: tuple[SectorStrength, ...]
    symbol_sector: Mapping[str, str]
    generated_from: str


def _rsi(closes: Sequence[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains=[]; losses=[]
    for previous, current in zip(closes[-period-1:-1], closes[-period:]):
        delta=current-previous; gains.append(max(delta,0.0)); losses.append(max(-delta,0.0))
    average_gain=sum(gains)/period; average_loss=sum(losses)/period
    if average_loss == 0: return 100.0 if average_gain else 50.0
    return 100.0-100.0/(1.0+average_gain/average_loss)


def _sma(closes: Sequence[float], period: int) -> float | None:
    return sum(closes[-period:])/period if len(closes)>=period else None


def _pct(current: float, previous: float | None) -> float | None:
    if previous is None or previous == 0: return None
    return (current/previous-1.0)*100.0


def _lookback_close(bars: Sequence[ResearchBar], days: int) -> float | None:
    """Return the latest close at least `days` calendar days before the last bar."""
    ordered=tuple(sorted(bars,key=lambda item:item.timestamp))
    if len(ordered)<2: return None
    target=ordered[-1].timestamp-timedelta(days=days)
    eligible=[bar for bar in ordered[:-1] if bar.timestamp<=target]
    if eligible: return eligible[-1].close
    return ordered[0].close if ordered[0].timestamp<ordered[-1].timestamp else None


def _index_regime(symbol: str, label: str, bars: Sequence[ResearchBar]) -> IndexRegime:
    ordered=tuple(sorted(bars,key=lambda item:item.timestamp))
    if len(ordered)<20: raise ResearchMarketDataError(f"Insufficient history for {label} regime: {len(ordered)} bars.")
    closes=[float(bar.close) for bar in ordered]; close=closes[-1]
    sma20=_sma(closes,20); sma50=_sma(closes,50); rsi14=_rsi(closes,14)
    one_day=_pct(close,_lookback_close(ordered,1)); five_day=_pct(close,_lookback_close(ordered,5))
    bullish_votes=bearish_votes=0
    if sma20 is not None: bullish_votes+=int(close>=sma20); bearish_votes+=int(close<sma20)
    if sma50 is not None: bullish_votes+=int(close>=sma50); bearish_votes+=int(close<sma50)
    if sma20 is not None and sma50 is not None: bullish_votes+=int(sma20>=sma50); bearish_votes+=int(sma20<sma50)
    if rsi14 is not None: bullish_votes+=int(rsi14>=50); bearish_votes+=int(rsi14<50)
    if one_day is not None: bullish_votes+=int(one_day>=0); bearish_votes+=int(one_day<0)
    total=max(1,bullish_votes+bearish_votes); score=(bullish_votes-bearish_votes)/total
    regime="BULLISH" if score>=0.30 else "BEARISH" if score<=-0.30 else "MIXED"
    reason=f"close={close:.2f}; SMA20={sma20:.2f}; SMA50={sma50:.2f}; RSI14={rsi14:.1f}; votes={bullish_votes}/{bearish_votes}"
    return IndexRegime(symbol,label,close,one_day,five_day,sma20,sma50,rsi14,regime,score,reason)


def _sector_for(symbol: str) -> str:
    return SECTOR_MAP.get(symbol.strip().upper(),"OTHER")


def _sector_strength(rows: Mapping[str, Sequence[ResearchBar]]) -> tuple[SectorStrength,...]:
    buckets:dict[str,list[tuple[str,Sequence[ResearchBar]]]]={}
    for symbol,bars in rows.items(): buckets.setdefault(_sector_for(symbol),[]).append((symbol,bars))
    result=[]
    for sector,members in buckets.items():
        one_day=[]; five_day=[]; bullish=0
        for symbol,bars in members:
            ordered=tuple(sorted(bars,key=lambda item:item.timestamp))
            if len(ordered)<2: continue
            close=float(ordered[-1].close)
            d1=_pct(close,_lookback_close(ordered,1)); d5=_pct(close,_lookback_close(ordered,5))
            if d1 is not None: one_day.append(d1); bullish+=int(d1>=0)
            if d5 is not None: five_day.append(d5)
        avg1=sum(one_day)/len(one_day) if one_day else None; avg5=sum(five_day)/len(five_day) if five_day else None
        breadth=bullish/len(one_day) if one_day else 0.5
        return_score=max(-1.0,min(1.0,(avg5 or 0.0)/3.0)); breadth_score=(breadth-0.5)*2.0
        score=0.55*return_score+0.45*breadth_score
        regime="STRONG" if score>=0.20 else "WEAK" if score<=-0.20 else "NEUTRAL"
        result.append(SectorStrength(sector,tuple(symbol for symbol,_ in members),breadth*100.0,avg1,avg5,score,regime))
    return tuple(sorted(result,key=lambda item:item.score,reverse=True))


def build_market_context(rows: Mapping[str, Sequence[ResearchBar]], *, provider: YahooFinanceMarketDataProvider | None = None) -> MarketContext:
    index_provider=provider or YahooFinanceMarketDataProvider()
    nifty_bars=index_provider.history("^NSEI",period="1y",interval="1d")
    bank_bars=index_provider.history("^NSEBANK",period="1y",interval="1d")
    nifty=_index_regime("^NSEI","NIFTY 50",nifty_bars); bank=_index_regime("^NSEBANK","BANK NIFTY",bank_bars)
    combined_score=(nifty.score+bank.score)/2.0
    if nifty.regime=="BULLISH" and bank.regime=="BULLISH": combined="BULLISH"
    elif nifty.regime=="BEARISH" and bank.regime=="BEARISH": combined="BEARISH"
    elif combined_score>=0.25: combined="BULLISH"
    elif combined_score<=-0.25: combined="BEARISH"
    else: combined="MIXED"
    return MarketContext(nifty,bank,combined,combined_score,_sector_strength(rows),{symbol:_sector_for(symbol) for symbol in rows},"NIFTY 50 + BANK NIFTY daily regime; scanned-stock relative sector strength")


def candidate_context_allowed(direction: str, context: MarketContext, sector: str | None = None) -> tuple[bool,str]:
    side=direction.strip().upper()
    if side not in {"BUY","SELL"}: return False,"Invalid direction."
    if context.combined_regime=="BULLISH" and side=="SELL": return False,"SELL blocked by bullish NIFTY/BANK NIFTY regime."
    if context.combined_regime=="BEARISH" and side=="BUY": return False,"BUY blocked by bearish NIFTY/BANK NIFTY regime."
    if sector:
        strength=next((item for item in context.sector_strength if item.sector==sector),None)
        if strength is not None:
            if side=="BUY" and strength.regime=="WEAK": return False,f"BUY blocked by weak {sector} sector strength."
            if side=="SELL" and strength.regime=="STRONG": return False,f"SELL blocked by strong {sector} sector strength."
    return True,"Market regime and sector context aligned."
