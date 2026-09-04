from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests


DHAN_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
INDEX_NAMES = {
    "NIFTY_50": {
        "NIFTY 50", "NIFTY50", "NIFTY", "NIFTY_50", "NIFTY-50",
    },
    "BANK_NIFTY": {
        "NIFTY BANK", "BANKNIFTY", "NIFTYBANK", "BANK NIFTY", "NIFTY_BANK",
    },
}


def _normalise(value: Any) -> str:
    return " ".join(str(value or "").strip().upper().replace("_", " ").split())


def _resolve_indices(timeout: int = 30) -> dict[str, dict[str, str]]:
    """Resolve current NSE index IDs from Dhan's official scrip master.

    Dhan's master can represent an index name in either SEM_TRADING_SYMBOL or
    SEM_CUSTOM_SYMBOL, and NIFTY 50 has appeared with short aliases such as
    NIFTY.  We inspect both fields and only accept genuine NSE INDEX rows.
    """
    response = requests.get(
        DHAN_MASTER_URL,
        timeout=timeout,
        headers={"User-Agent": "stockmarket-bot/1.0"},
    )
    response.raise_for_status()
    reader = csv.DictReader(io.StringIO(response.content.decode("utf-8-sig", errors="replace")))
    fieldnames = reader.fieldnames or []
    fields = {_normalise(x): x for x in fieldnames if x}

    required = (
        "SEM EXM EXCH ID",
        "SEM SEGMENT",
        "SEM INSTRUMENT NAME",
        "SEM TRADING SYMBOL",
        "SEM SMST SECURITY ID",
    )
    missing = [x for x in required if x not in fields]
    if missing:
        raise RuntimeError("DHAN_INDEX_MASTER_INVALID: " + ",".join(missing))

    custom_field = fields.get("SEM CUSTOM SYMBOL")
    out: dict[str, dict[str, str]] = {}

    for row in reader:
        exchange = _normalise(row.get(fields["SEM EXM EXCH ID"], ""))
        segment = _normalise(row.get(fields["SEM SEGMENT"], ""))
        instrument = _normalise(row.get(fields["SEM INSTRUMENT NAME"], ""))
        trading_symbol = _normalise(row.get(fields["SEM TRADING SYMBOL"], ""))
        custom_symbol = _normalise(row.get(custom_field, "")) if custom_field else ""
        security_id = str(row.get(fields["SEM SMST SECURITY ID"], "")).strip()

        if exchange != "NSE" or not security_id:
            continue
        if segment not in {"I", "INDEX"} and "INDEX" not in instrument:
            continue
        if "INDEX" not in instrument:
            continue

        names = {trading_symbol, custom_symbol} - {""}
        key = None
        for candidate, aliases in INDEX_NAMES.items():
            if names & {_normalise(x) for x in aliases}:
                key = candidate
                break
        if key is None:
            continue

        out[key] = {
            "symbol": custom_symbol or trading_symbol,
            "trading_symbol": trading_symbol,
            "security_id": security_id,
            "exchange_segment": "NSE_IDX",
            "instrument": "INDEX",
        }

    missing = [key for key in INDEX_NAMES if key not in out]
    if missing:
        raise RuntimeError("DHAN_INDEX_NOT_FOUND: " + ",".join(missing))
    return out


def _rsi(series: pd.Series, period: int = 14) -> float | None:
    close = pd.to_numeric(series, errors="coerce").dropna()
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().iloc[-1]
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().iloc[-1]
    if pd.isna(avg_gain) or pd.isna(avg_loss):
        return None
    if float(avg_loss) == 0:
        return 100.0 if float(avg_gain) > 0 else 50.0
    return float(100 - (100 / (1 + float(avg_gain) / float(avg_loss))))


def _analyse_index(name: str, frame: pd.DataFrame) -> dict[str, Any]:
    if frame is None or frame.empty or "close" not in frame:
        raise RuntimeError(f"INDEX_DATA_UNAVAILABLE:{name}")
    x = frame.copy()
    x["close"] = pd.to_numeric(x["close"], errors="coerce")
    x = x.dropna(subset=["close"])
    if "timestamp" in x.columns:
        x = x.sort_values("timestamp")
    if len(x) < 50:
        raise RuntimeError(f"INDEX_DATA_INSUFFICIENT:{name}:{len(x)}")

    close = x["close"].reset_index(drop=True)
    price = float(close.iloc[-1])
    previous = float(close.iloc[-2])
    ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
    ret5 = float(close.pct_change(5).iloc[-1] * 100)
    ret20 = float(close.pct_change(20).iloc[-1] * 100)
    rsi = _rsi(close)

    score = 5.0
    score += 1.2 if price > ema20 else -1.2
    score += 1.2 if ema20 > ema50 else -1.2
    score += 0.6 if ret5 > 0 else -0.6 if ret5 < 0 else 0
    score += 0.6 if ret20 > 0 else -0.6 if ret20 < 0 else 0
    if rsi is not None:
        score += 0.8 if rsi >= 55 else -0.8 if rsi <= 45 else 0
    score = max(0.0, min(10.0, score))
    state = "BULLISH" if score >= 6.5 else "BEARISH" if score <= 3.5 else "NEUTRAL"

    return {
        "name": name,
        "price": price,
        "previous_close": previous,
        "change_pct": round((price / previous - 1) * 100, 4) if previous else 0.0,
        "ema20": round(ema20, 4),
        "ema50": round(ema50, 4),
        "rsi": round(rsi, 4) if rsi is not None else None,
        "return_5_pct": round(ret5, 4),
        "return_20_pct": round(ret20, 4),
        "score": round(score, 3),
        "state": state,
        "bars": len(x),
    }


def build(broker, cache_path: str = "data/market_regime.json") -> dict[str, Any]:
    indices = _resolve_indices()
    nifty = _analyse_index(
        "NIFTY 50",
        broker.history(indices["NIFTY_50"]["security_id"], "NSE_IDX", 5, instrument="INDEX"),
    )
    bank = _analyse_index(
        "BANK NIFTY",
        broker.history(indices["BANK_NIFTY"]["security_id"], "NSE_IDX", 5, instrument="INDEX"),
    )

    if nifty["state"] == "BULLISH" and bank["state"] == "BULLISH":
        combined = "BULLISH"
    elif nifty["state"] == "BEARISH" and bank["state"] == "BEARISH":
        combined = "BEARISH"
    else:
        combined = "MIXED"

    result: dict[str, Any] = {
        "status": "AVAILABLE",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "indices": {"NIFTY_50": nifty, "BANK_NIFTY": bank},
        "combined_regime": combined,
        "buy_allowed": combined == "BULLISH",
        "sell_allowed": combined == "BEARISH",
        "reason": f"NIFTY={nifty['state']} ({nifty['score']:.2f}), BANK_NIFTY={bank['state']} ({bank['score']:.2f}), combined={combined}",
    }
    path = Path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
