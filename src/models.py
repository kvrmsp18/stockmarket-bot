from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class MarketSnapshot:
    symbol: str
    exchange: str
    timestamp: datetime
    last_price: float
    previous_close: Optional[float] = None
    volume: Optional[int] = None
    bid: Optional[float] = None
    ask: Optional[float] = None

@dataclass
class TradeCandidate:
    symbol: str
    exchange: str
    direction: str
    entry: float
    stop_loss: float
    target: float
    risk_per_share: float
    potential_per_share: float
    potential_percent: float
    risk_reward: float
    confidence: float
    reason: str
    liquidity_ok: bool
    market_regime_ok: bool
    news_status: str
    holding_window: str
