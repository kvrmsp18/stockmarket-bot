from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from .brokers import DhanBroker


IST = ZoneInfo("Asia/Kolkata")


def extended_daily_history(
    broker: DhanBroker,
    security_id: str,
    exchange_segment: str = "NSE_EQ",
    instrument: str = "EQUITY",
    calendar_days: int = 700,
) -> pd.DataFrame:
    """Fetch enough genuine daily candles for 14-period monthly RSI.

    Dhan may cap a historical-chart request by date span. Fetch two sub-year
    windows, merge them, and deduplicate timestamps. No synthetic candles are
    created and no daily data is substituted with intraday data.
    """
    end: date = datetime.now(IST).date()
    start = end - timedelta(days=max(365, int(calendar_days)))
    split = start + timedelta(days=(end - start).days // 2)
    windows = ((start, split), (split + timedelta(days=1), end))
    frames: list[pd.DataFrame] = []

    for from_date, to_date in windows:
        if from_date > to_date:
            continue
        payload = broker._daily_history_payload(
            security_id,
            exchange_segment,
            instrument,
            from_date.isoformat(),
            to_date.isoformat(),
        )
        data = broker._request("POST", "/v2/charts/historical", json=payload)
        frame = broker._frame(data.get("data", data) if isinstance(data, dict) else {})
        if not frame.empty:
            frames.append(frame)

    if not frames:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    out = pd.concat(frames, ignore_index=True)
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
    out = out.dropna(subset=["timestamp", "close"])
    return out.drop_duplicates(subset=["timestamp"], keep="last").sort_values("timestamp").reset_index(drop=True)
