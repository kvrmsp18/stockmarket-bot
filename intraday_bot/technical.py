from __future__ import annotations

import numpy as np
import pandas as pd


def _wilder_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    close = pd.to_numeric(series, errors="coerce")
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.where(avg_loss != 0, 100.0)
    return rsi


def indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate core indicators using non-look-ahead Wilder-style smoothing where appropriate."""
    x = df.copy()
    close = pd.to_numeric(x["close"], errors="coerce")
    high = pd.to_numeric(x["high"], errors="coerce")
    low = pd.to_numeric(x["low"], errors="coerce")
    volume = pd.to_numeric(x["volume"], errors="coerce").fillna(0.0)
    x["ema20"] = close.ewm(span=20, adjust=False).mean()
    x["ema50"] = close.ewm(span=50, adjust=False).mean()
    x["sma20"] = close.rolling(20).mean()

    if "timestamp" in x.columns:
        ts = pd.to_datetime(x["timestamp"], utc=True, errors="coerce")
        session = ts.dt.tz_convert("Asia/Kolkata").dt.date
        typical = (high + low + close) / 3.0
        pv = typical * volume
        x["vwap"] = (pv.groupby(session).cumsum() / volume.groupby(session).cumsum().replace(0, np.nan)).ffill()
    else:
        x["vwap"] = (close * volume).cumsum() / volume.replace(0, np.nan).cumsum()
        x["vwap"] = x["vwap"].ffill()

    x["rsi"] = _wilder_rsi(close, 14)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    x["macd"] = ema12 - ema26
    x["macd_signal"] = x["macd"].ewm(span=9, adjust=False).mean()

    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    x["atr"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    x["bb_mid"] = mid
    x["bb_upper"] = mid + 2 * std
    x["bb_lower"] = mid - 2 * std

    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    atr_w = x["atr"].replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / atr_w
    minus_di = 100 * minus_dm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / atr_w
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    x["adx"] = dx.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    x["rel_volume"] = volume / volume.rolling(20).mean().replace(0, np.nan)
    x["return_5"] = close.pct_change(5)
    x["return_20"] = close.pct_change(20)
    return x


def _period_rsi(daily: pd.DataFrame, rule: str) -> float | None:
    if daily is None or daily.empty or "close" not in daily:
        return None
    x = daily.copy()
    x["close"] = pd.to_numeric(x["close"], errors="coerce")
    if "timestamp" not in x.columns:
        return None
    x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True, errors="coerce")
    x = x.dropna(subset=["timestamp", "close"]).sort_values("timestamp").set_index("timestamp")
    if x.empty:
        return None
    close = x["close"].resample(rule).last().dropna()
    rsi = _wilder_rsi(close, 14).dropna()
    return float(rsi.iloc[-1]) if not rsi.empty else None


def multi_timeframe_rsi(daily: pd.DataFrame) -> dict[str, float | None]:
    """Return genuine daily, weekly and monthly RSI(14) from daily candles."""
    if daily is None or daily.empty:
        return {"daily": None, "weekly": None, "monthly": None}
    x = daily.copy()
    x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True, errors="coerce")
    x = x.dropna(subset=["timestamp"]).sort_values("timestamp")
    daily_rsi_series = _wilder_rsi(pd.to_numeric(x["close"], errors="coerce"), 14).dropna()
    return {
        "daily": float(daily_rsi_series.iloc[-1]) if not daily_rsi_series.empty else None,
        "weekly": _period_rsi(x, "W-FRI"),
        "monthly": _period_rsi(x, "ME"),
    }


def mtf_rsi_agreement(rsi: dict[str, float | None], direction: str) -> tuple[bool, str]:
    """Require all three MTF RSI values to agree with the trade direction."""
    values = [rsi.get("monthly"), rsi.get("weekly"), rsi.get("daily")]
    if any(v is None for v in values):
        return False, "MTF_RSI_DATA_UNAVAILABLE"
    monthly, weekly, daily = (float(v) for v in values)
    if direction == "BUY":
        ok = monthly >= 50 and weekly >= 50 and daily >= 55
        return ok, "MTF_RSI_BULLISH" if ok else "MTF_RSI_NOT_BULLISH"
    if direction == "SELL":
        ok = monthly <= 50 and weekly <= 50 and daily <= 45
        return ok, "MTF_RSI_BEARISH" if ok else "MTF_RSI_NOT_BEARISH"
    return False, "MTF_RSI_NO_DIRECTION"


def trend_score(row: pd.Series) -> float:
    """Transparent 0-10 score."""
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


def technical_setup(df: pd.DataFrame, daily_history: pd.DataFrame | None = None) -> dict:
    x = indicators(df)
    row = x.iloc[-1]
    score = trend_score(row)
    previous_score = trend_score(x.iloc[-2]) if len(x) >= 2 else None
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
    risk = abs(price - stop)
    reward = abs(target - price)
    mtf = multi_timeframe_rsi(daily_history) if daily_history is not None else {"daily": None, "weekly": None, "monthly": None}
    mtf_ok, mtf_reason = mtf_rsi_agreement(mtf, direction)
    indicators_snapshot = {
        k: (None if pd.isna(row.get(k)) else float(row.get(k)))
        for k in ("rsi", "macd", "macd_signal", "adx", "atr", "vwap", "ema20", "ema50", "rel_volume")
    }
    return {
        "price": price,
        "direction": direction,
        "trend_score": score,
        "trend_state": state,
        "transition": transition(previous_score, score),
        "technical_score": score,
        "atr": atr,
        "entry": price,
        "entry_low": price - risk * 0.25,
        "entry_high": price + risk * 0.25,
        "max_chase": price + risk * 0.5 if direction == "BUY" else price - risk * 0.5,
        "stop": stop,
        "target": target,
        "risk": risk,
        "reward": reward,
        "rr": reward / risk if risk else 0.0,
        "indicators": indicators_snapshot,
        "mtf_rsi": mtf,
        "mtf_rsi_agreement": mtf_ok,
        "mtf_rsi_reason": mtf_reason,
    }
