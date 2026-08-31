from __future__ import annotations

import numpy as np
import pandas as pd


def indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate configurable core indicators without look-ahead."""
    x = df.copy()
    close = x["close"].astype(float)
    high = x["high"].astype(float)
    low = x["low"].astype(float)
    volume = x["volume"].astype(float)
    x["ema20"] = close.ewm(span=20, adjust=False).mean()
    x["ema50"] = close.ewm(span=50, adjust=False).mean()
    x["sma20"] = close.rolling(20).mean()
    x["vwap"] = (close.mul(volume).cumsum() / volume.replace(0, np.nan).cumsum()).ffill()
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    x["rsi"] = 100 - (100 / (1 + rs))
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    x["macd"] = ema12 - ema26
    x["macd_signal"] = x["macd"].ewm(span=9, adjust=False).mean()
    tr = pd.concat([(high-low), (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
    x["atr"] = tr.rolling(14).mean()
    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    x["bb_mid"] = mid
    x["bb_upper"] = mid + 2 * std
    x["bb_lower"] = mid - 2 * std
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    atr = x["atr"].replace(0, np.nan)
    plus_di = 100 * plus_dm.rolling(14).sum() / atr
    minus_di = 100 * minus_dm.rolling(14).sum() / atr
    dx = 100 * (plus_di-minus_di).abs() / (plus_di+minus_di).replace(0, np.nan)
    x["adx"] = dx.rolling(14).mean()
    x["rel_volume"] = volume / volume.rolling(20).mean().replace(0, np.nan)
    x["return_5"] = close.pct_change(5)
    x["return_20"] = close.pct_change(20)
    return x


def trend_score(row: pd.Series) -> float:
    """Transparent 0-10 score. Source thresholds remain >7 bullish, <4 bearish."""
    score = 5.0
    if row.get("close", np.nan) > row.get("vwap", np.nan): score += 0.8
    if row.get("close", np.nan) > row.get("ema20", np.nan): score += 0.8
    if row.get("ema20", np.nan) > row.get("ema50", np.nan): score += 0.8
    if row.get("rsi", 50) > 55: score += 0.5
    if row.get("rsi", 50) < 45: score -= 0.5
    if row.get("macd", 0) > row.get("macd_signal", 0): score += 0.5
    else: score -= 0.5
    if row.get("adx", 0) >= 20: score += 0.5
    if row.get("rel_volume", 1) >= 1.5: score += 0.5
    if row.get("return_5", 0) > 0: score += 0.3
    elif row.get("return_5", 0) < 0: score -= 0.3
    return float(max(0, min(10, score)))


def trend_state(score: float) -> str:
    if score > 7: return "BULLISH"
    if score < 4: return "BEARISH"
    return "NEUTRAL"


def transition(previous: float | None, current: float) -> str | None:
    if previous is None: return None
    a, b = trend_state(previous), trend_state(current)
    if a != b and b == "BULLISH": return "SHIFT TO BULLISH"
    if a != b and b == "BEARISH": return "SHIFT TO BEARISH"
    return None


def technical_setup(df: pd.DataFrame) -> dict:
    x = indicators(df)
    row = x.iloc[-1]
    score = trend_score(row)
    state = trend_state(score)
    atr = float(row.get("atr", 0) or 0)
    price = float(row["close"])
    bullish = state == "BULLISH" and price >= float(row.get("vwap", price))
    bearish = state == "BEARISH" and price <= float(row.get("vwap", price))
    direction = "BUY" if bullish else "SELL" if bearish else "HOLD"
    if direction == "BUY":
        stop = price - max(atr * 1.2, price * 0.004)
        target = price + max(atr * 3.6, price * 0.012)
    elif direction == "SELL":
        stop = price + max(atr * 1.2, price * 0.004)
        target = price - max(atr * 3.6, price * 0.012)
    else:
        stop = target = price
    risk = abs(price-stop)
    reward = abs(target-price)
    return {"price": price, "direction": direction, "trend_score": score, "trend_state": state, "transition": None, "technical_score": score, "atr": atr, "entry": price, "entry_low": price-risk*0.25, "entry_high": price+risk*0.25, "max_chase": price+risk*0.5 if direction=="BUY" else price-risk*0.5, "stop": stop, "target": target, "risk": risk, "reward": reward, "rr": reward/risk if risk else 0.0, "indicators": {k: (None if pd.isna(row.get(k)) else float(row.get(k))) for k in ("rsi","macd","macd_signal","adx","atr","vwap","ema20","ema50","rel_volume")}}
