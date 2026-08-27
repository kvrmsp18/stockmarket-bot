"""Read-only NSE/BSE chart history with DhanHQ as the primary source."""
from __future__ import annotations

import csv
import io
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

import requests

from .dhan_api import DhanAPIError, DhanAuthenticationError, DhanHQClient
from .research_market_data import ResearchBar, ResearchMarketDataError, ResilientMarketDataProvider

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class ChartTimeframe:
    key: str
    label: str
    period_days: int
    interval: str
    intraday: bool


TIMEFRAMES: tuple[ChartTimeframe, ...] = (
    ChartTimeframe("24H", "24H", 2, "5", True),
    ChartTimeframe("7D", "7D", 7, "15", True),
    ChartTimeframe("1M", "1M", 30, "60", True),
    ChartTimeframe("3M", "3M", 90, "1D", False),
    ChartTimeframe("6M", "6M", 180, "1D", False),
    ChartTimeframe("12M", "12M", 365, "1D", False),
)
_TIMEFRAME_BY_KEY = {item.key: item for item in TIMEFRAMES}


class DhanChartDataError(ResearchMarketDataError):
    """Raised when Dhan chart/instrument data cannot be used."""


@dataclass(frozen=True)
class Instrument:
    symbol: str
    exchange: str
    security_id: int


class DhanChartProvider:
    """DhanHQ read-only chart provider for intraday and historical bars."""

    MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
    name = "dhanhq"

    def __init__(self, client: DhanHQClient | None = None, timeout: float = 15.0) -> None:
        self.client = client or DhanHQClient(timeout=timeout)
        self.timeout = timeout
        self.session = self.client.session

    @property
    def configured(self) -> bool:
        return self.client.configured

    def history(self, symbol: str, timeframe: ChartTimeframe) -> tuple[ResearchBar, ...]:
        if not self.configured:
            raise DhanChartDataError("Dhan is not configured for chart data.")

        instrument = self._resolve_instrument(symbol)
        today = datetime.now(IST).date()
        start = today - timedelta(days=timeframe.period_days)
        endpoint = "/charts/intraday" if timeframe.intraday else "/charts/historical"
        body: dict[str, Any] = {
            "securityId": str(instrument.security_id),
            "exchangeSegment": instrument.exchange,
            "instrument": "EQUITY",
            "expiryCode": 0,
            "fromDate": start.isoformat(),
            "toDate": today.isoformat(),
        }
        if timeframe.intraday:
            body["interval"] = int(timeframe.interval)

        payload = self.client._request("POST", endpoint, json=body)
        bars = self._parse_bars(symbol, payload)
        if not bars:
            raise DhanChartDataError(
                f"Dhan returned no chart bars for {symbol} / {timeframe.key}."
            )
        return tuple(sorted(bars, key=lambda bar: bar.timestamp))

    def _resolve_instrument(self, symbol: str) -> Instrument:
        """Resolve a dashboard NSE/BSE symbol to Dhan's cash-equity security ID."""
        normalized = symbol.strip().upper()
        base = normalized.rsplit(".", 1)[0]
        exchange_hint = "BSE_EQ" if normalized.endswith(".BO") else "NSE_EQ"
        target_key = _normalize_equity_symbol(base)

        mapping = _load_explicit_security_map()
        for candidate in (normalized, base, target_key):
            item = mapping.get(candidate)
            if item:
                return Instrument(candidate, item["exchange"], int(item["security_id"]))

        master = _load_dhan_instrument_master(self.session, self.timeout)

        exact = [
            row for row in master
            if row.exchange == exchange_hint and row.symbol.upper() == base
        ]
        if exact:
            return exact[0]

        normalized_matches = [
            row for row in master
            if row.exchange == exchange_hint
            and _normalize_equity_symbol(row.symbol) == target_key
        ]
        if normalized_matches:
            return normalized_matches[0]

        raise DhanChartDataError(
            f"No Dhan {exchange_hint} security ID was found for {normalized} "
            f"(normalized equity symbol: {target_key})."
        )

    @staticmethod
    def _parse_bars(symbol: str, payload: object) -> list[ResearchBar]:
        if not isinstance(payload, dict):
            raise DhanChartDataError(f"Invalid Dhan chart response for {symbol}.")

        data: Any = payload.get("data", payload)
        if isinstance(data, dict):
            ts = data.get("timestamp") or data.get("timestamps") or data.get("time")
            op = data.get("open") or data.get("opens")
            hi = data.get("high") or data.get("highs")
            lo = data.get("low") or data.get("lows")
            cl = data.get("close") or data.get("closes")
            vol = data.get("volume") or data.get("volumes") or []

            if all(isinstance(v, list) for v in (ts, op, hi, lo, cl)):
                return DhanChartProvider._bars_from_arrays(symbol, ts, op, hi, lo, cl, vol)

            for key in ("candles", "bars", "data"):
                candidate = data.get(key)
                if isinstance(candidate, list):
                    data = candidate
                    break

        if isinstance(data, list):
            return DhanChartProvider._bars_from_rows(symbol, data)

        raise DhanChartDataError(
            f"Dhan chart response contained no OHLCV series for {symbol}."
        )

    @staticmethod
    def _bars_from_arrays(symbol: str, timestamps: list[Any], opens: list[Any], highs: list[Any], lows: list[Any], closes: list[Any], volumes: list[Any]) -> list[ResearchBar]:
        bars: list[ResearchBar] = []
        for i, raw_ts in enumerate(timestamps):
            try:
                bars.append(
                    _make_bar(
                        symbol,
                        _parse_timestamp(raw_ts),
                        opens[i],
                        highs[i],
                        lows[i],
                        closes[i],
                        volumes[i] if i < len(volumes) else 0,
                    )
                )
            except (IndexError, TypeError, ValueError, OverflowError, ResearchMarketDataError):
                continue
        return bars

    @staticmethod
    def _bars_from_rows(symbol: str, rows: list[Any]) -> list[ResearchBar]:
        bars: list[ResearchBar] = []
        for row in rows:
            if isinstance(row, dict):
                raw_ts = row.get("timestamp") or row.get("time") or row.get("date")
                values = (
                    row.get("open"), row.get("high"), row.get("low"), row.get("close"), row.get("volume", 0),
                )
            elif isinstance(row, (list, tuple)) and len(row) >= 5:
                raw_ts = row[0]
                values = (row[1], row[2], row[3], row[4], row[5] if len(row) > 5 else 0)
            else:
                continue
            try:
                bars.append(_make_bar(symbol, _parse_timestamp(raw_ts), *values))
            except (TypeError, ValueError, OverflowError, ResearchMarketDataError):
                continue
        return bars


def _normalize_equity_symbol(value: str) -> str:
    """Normalize dashboard/master symbols to a cash-equity comparison key."""
    text = str(value or "").strip().upper()
    if "." in text:
        text = text.rsplit(".", 1)[0]
    for suffix in ("-EQ", "_EQ", "-BE", "_BE", "-SM", "_SM", "-ST", "_ST"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return "".join(ch for ch in text if ch.isalnum())


def _make_bar(symbol: str, timestamp: datetime, o: Any, h: Any, l: Any, c: Any, volume: Any = 0) -> ResearchBar:
    try:
        values = tuple(map(float, (o, h, l, c)))
        v = int(float(volume or 0))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ResearchMarketDataError(f"Malformed OHLCV data for {symbol}.") from exc
    if min(values) <= 0 or v < 0 or values[1] < max(values[0], values[2], values[3]) or values[2] > min(values[0], values[1], values[3]):
        raise ResearchMarketDataError(f"Invalid OHLCV data for {symbol}.")
    return ResearchBar(symbol, timestamp.astimezone(IST), values[0], values[1], values[2], values[3], v)


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000.0
        return datetime.fromtimestamp(number, tz=timezone.utc).astimezone(IST)
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


@lru_cache(maxsize=1)
def _load_dhan_instrument_master_cached(url: str, timeout: float) -> tuple[Instrument, ...]:
    response = requests.get(url, timeout=timeout, headers={"User-Agent": "nse-bse-intraday-ai/5.0"})
    response.raise_for_status()
    reader = csv.DictReader(io.StringIO(response.content.decode("utf-8-sig", errors="replace")))
    fields = {f.strip().lower(): f for f in (reader.fieldnames or []) if f}
    # Dhan's current master uses SEM_SECURITY_ID. Older code looked for
    # SEM_SMST_SECURITY_ID, which caused valid NSE/BSE equities such as WIPRO
    # and TMPV to appear as if they had no security ID.
    exch = _first_field(fields, "sem_exm_exch_id", "exchange", "exchange_id")
    sec = _first_field(fields, "sem_security_id", "sem_smst_security_id", "security_id", "securityid")
    sym = _first_field(fields, "sem_trading_symbol", "trading_symbol", "tradingsymbol", "sem_custom_symbol")
    if not exch or not sec or not sym:
        raise DhanChartDataError("Dhan instrument master schema is missing exchange/security/symbol fields.")

    result: list[Instrument] = []
    for row in reader:
        exchange = str(row.get(exch, "")).strip().upper()
        if exchange not in {"NSE", "BSE"}:
            continue
        symbol = str(row.get(sym, "")).strip().upper()
        try:
            security_id = int(float(str(row.get(sec, "")).strip()))
        except (TypeError, ValueError):
            continue
        if symbol:
            result.append(Instrument(symbol, f"{exchange}_EQ", security_id))

    if not result:
        raise DhanChartDataError("Dhan instrument master contains no NSE/BSE instruments.")
    return tuple(result)


def _first_field(fields: dict[str, str], *names: str) -> str | None:
    for name in names:
        if name in fields:
            return fields[name]
    return None


def _load_dhan_instrument_master(session: requests.Session, timeout: float) -> tuple[Instrument, ...]:
    return _load_dhan_instrument_master_cached(DhanChartProvider.MASTER_URL, timeout)


def _load_explicit_security_map() -> dict[str, dict[str, Any]]:
    raw = os.getenv("DHAN_SECURITY_IDS_JSON", "").strip()
    if not raw:
        try:
            import streamlit as st
            raw = str(st.secrets.get("DHAN_SECURITY_IDS_JSON", "")).strip()
        except Exception:
            raw = ""
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for symbol, item in payload.items():
        if not isinstance(item, dict) or item.get("security_id") is None:
            continue
        try:
            exchange = str(item.get("exchange", "NSE_EQ")).upper()
            security_id = int(item["security_id"])
            raw_symbol = str(symbol).upper()
            result[raw_symbol] = {"exchange": exchange, "security_id": security_id}
            result.setdefault(_normalize_equity_symbol(raw_symbol), {"exchange": exchange, "security_id": security_id})
        except (TypeError, ValueError):
            continue
    return result


def get_timeframe(key: str) -> ChartTimeframe:
    normalized = str(key).strip().upper()
    if normalized not in _TIMEFRAME_BY_KEY:
        raise ValueError(f"Unsupported chart timeframe '{key}'. Use: {', '.join(_TIMEFRAME_BY_KEY)}.")
    return _TIMEFRAME_BY_KEY[normalized]


def load_chart_history(symbol: str, timeframe: str, *, provider: ResilientMarketDataProvider | None = None) -> tuple[ResearchBar, ...]:
    normalized = symbol.strip().upper()
    config = get_timeframe(timeframe)
    dhan_error: Exception | None = None
    try:
        bars = DhanChartProvider().history(normalized, config)
        if bars:
            return bars
    except (DhanAPIError, DhanAuthenticationError, DhanChartDataError, requests.RequestException, ValueError) as exc:
        dhan_error = exc

    data_provider = provider or ResilientMarketDataProvider()
    try:
        bars = data_provider.history(normalized, period=_fallback_period(config), interval=_fallback_interval(config))
        if not bars:
            raise ResearchMarketDataError("Fallback returned no chart bars.")
        return tuple(sorted(bars, key=lambda bar: bar.timestamp))
    except Exception as fallback_exc:
        detail = f"Dhan chart source failed: {dhan_error}" if dhan_error else "Dhan chart source unavailable"
        raise ResearchMarketDataError(f"{detail}; fallback failed: {fallback_exc}") from fallback_exc


def load_all_chart_history(symbol: str, *, provider: ResilientMarketDataProvider | None = None) -> dict[str, tuple[ResearchBar, ...]]:
    """Load every dashboard chart timeframe independently."""
    return {timeframe.key: load_chart_history(symbol, timeframe.key, provider=provider) for timeframe in TIMEFRAMES}


def _fallback_period(config: ChartTimeframe) -> str:
    return {"24H": "1d", "7D": "7d", "1M": "1mo", "3M": "3mo", "6M": "6mo", "12M": "1y"}[config.key]


def _fallback_interval(config: ChartTimeframe) -> str:
    return {"24H": "5m", "7D": "15m", "1M": "1h", "3M": "1d", "6M": "1d", "12M": "1d"}[config.key]
