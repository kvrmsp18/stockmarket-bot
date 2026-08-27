"""Read-only Twelve Data intraday market-data fallback.

This provider is used only when the preferred DhanHQ intraday source is
unavailable. It never places orders and returns the same ResearchBar model
used by the research pipeline.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import requests

from .research_market_data import BaseMarketDataProvider, ResearchBar, ResearchMarketDataError


class TwelveDataMarketDataProvider(BaseMarketDataProvider):
    """Twelve Data time-series provider for Indian equities."""

    BASE_URL = "https://api.twelvedata.com/time_series"
    name = "twelvedata_intraday"

    def __init__(self, timeout: float = 12.0) -> None:
        super().__init__(timeout=timeout)
        self.api_key = self._read_api_key()

    @staticmethod
    def _read_api_key() -> str:
        value = os.getenv("TWELVEDATA_API_KEY", "").strip()
        if value:
            return value
        # Streamlit Cloud stores app secrets separately from process env.
        # Import lazily so GitHub Actions and normal Python tests do not need
        # Streamlit installed or configured.
        try:
            import streamlit as st  # type: ignore

            secret = st.secrets.get("TWELVEDATA_API_KEY", "")
            return str(secret).strip()
        except Exception:
            return ""

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def _symbol_and_exchange(symbol: str) -> tuple[str, str]:
        normalized = symbol.strip().upper()
        if normalized.endswith(".NS"):
            return normalized[:-3], "NSE"
        if normalized.endswith(".BO"):
            return normalized[:-3], "BSE"
        raise ResearchMarketDataError(
            f"Twelve Data requires an NSE/BSE symbol suffix: {normalized}"
        )

    @staticmethod
    def _interval(interval: str) -> str:
        mapping = {
            "1m": "1min",
            "5m": "5min",
            "15m": "15min",
            "30m": "30min",
            "45m": "45min",
            "1h": "1h",
            "60m": "1h",
        }
        try:
            return mapping[interval]
        except KeyError as exc:
            raise ResearchMarketDataError(
                f"Twelve Data does not support interval {interval}."
            ) from exc

    def history(
        self,
        symbol: str,
        *,
        period: str = "5d",
        interval: str = "15m",
    ) -> tuple[ResearchBar, ...]:
        if not self.configured:
            raise ResearchMarketDataError("Twelve Data API key is not configured.")

        ticker, exchange = self._symbol_and_exchange(symbol)
        td_interval = self._interval(interval)

        # outputsize is deliberately bounded. The research engine needs only
        # recent intraday bars and should not consume the provider quota with
        # unnecessary history.
        outputsize = 500
        if period in {"1d", "1D"}:
            outputsize = 100
        elif period in {"5d", "5D"}:
            outputsize = 500
        elif period in {"1mo", "1M"}:
            outputsize = 2000

        params = {
            "symbol": ticker,
            "exchange": exchange,
            "interval": td_interval,
            "outputsize": outputsize,
            "apikey": self.api_key,
            "format": "JSON",
            "timezone": "Asia/Kolkata",
        }

        try:
            response = self.session.get(self.BASE_URL, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise ResearchMarketDataError(
                f"Twelve Data network error: {exc}"
            ) from exc

        if not response.ok:
            raise ResearchMarketDataError(
                f"Twelve Data returned HTTP {response.status_code}."
            )

        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise ResearchMarketDataError("Twelve Data returned invalid JSON.") from exc

        if not isinstance(payload, dict):
            raise ResearchMarketDataError("Twelve Data returned an invalid payload.")

        if payload.get("status") == "error" or payload.get("code") not in (None, 200):
            message = payload.get("message") or payload.get("code") or "unknown API error"
            raise ResearchMarketDataError(f"Twelve Data API error: {message}")

        values = payload.get("values")
        if not isinstance(values, list):
            message = payload.get("message") or "no values returned"
            raise ResearchMarketDataError(f"Twelve Data returned no intraday data: {message}")

        bars: list[ResearchBar] = []
        for row in values:
            if not isinstance(row, dict):
                continue
            try:
                raw_dt = row.get("datetime")
                if not raw_dt:
                    continue
                timestamp = datetime.fromisoformat(str(raw_dt).replace("Z", "+00:00"))
                bars.append(
                    self._make_bar(
                        symbol.strip().upper(),
                        timestamp,
                        row.get("open"),
                        row.get("high"),
                        row.get("low"),
                        row.get("close"),
                        row.get("volume", 0),
                    )
                )
            except (TypeError, ValueError, OverflowError, ResearchMarketDataError):
                continue

        return self._validate(symbol.strip().upper(), bars)
