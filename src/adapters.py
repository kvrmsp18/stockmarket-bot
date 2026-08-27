from abc import ABC, abstractmethod
from typing import Callable, Iterable

from .dhan_api import DhanHQClient, load_security_map_from_env
from .models import MarketSnapshot


class MarketDataAdapter(ABC):
    """Common interface for all market-data providers."""

    @abstractmethod
    def connect(self) -> bool:
        """Connect to the market-data source."""
        raise NotImplementedError

    @abstractmethod
    def health(self) -> dict:
        """Return adapter health information."""
        raise NotImplementedError

    @abstractmethod
    def snapshot(self, symbols: Iterable[str]) -> list[MarketSnapshot]:
        """Return the latest market snapshot for requested symbols."""
        raise NotImplementedError


class HistoricalDataAdapter(MarketDataAdapter):
    """Historical-data adapter using an injected provider."""

    def __init__(self, provider: Callable[[str], MarketSnapshot] | None = None):
        self.provider = provider
        self._connected = False

    def connect(self) -> bool:
        self._connected = self.provider is not None
        return self._connected

    def health(self) -> dict:
        return {
            "status": "READY" if self.provider is not None else "NOT_CONFIGURED",
            "adapter": "historical",
        }

    def snapshot(self, symbols: Iterable[str]) -> list[MarketSnapshot]:
        if self.provider is None:
            raise RuntimeError("Historical data provider is not configured.")
        return [self.provider(symbol) for symbol in symbols]


class LiveDataAdapter(MarketDataAdapter):
    """Generic live NSE/BSE adapter using an injected provider."""

    def __init__(self, provider: Callable[[str], MarketSnapshot] | None = None):
        self.provider = provider
        self._connected = False

    def connect(self) -> bool:
        self._connected = self.provider is not None
        return self._connected

    def health(self) -> dict:
        return {
            "status": "READY" if self.provider is not None else "NOT_CONFIGURED",
            "adapter": "live",
        }

    def snapshot(self, symbols: Iterable[str]) -> list[MarketSnapshot]:
        if self.provider is None:
            raise RuntimeError("Live market-data provider is not configured.")
        return [self.provider(symbol) for symbol in symbols]


class DhanLiveDataAdapter(MarketDataAdapter):
    """Live market-data adapter backed by DhanHQ LTP API."""

    def __init__(
        self,
        client: DhanHQClient | None = None,
        security_map: dict[str, dict[str, int]] | None = None,
    ):
        self.client = client or DhanHQClient()
        self.security_map = security_map if security_map is not None else load_security_map_from_env()
        self._connected = False

    def connect(self) -> bool:
        health = self.client.health()
        self._connected = health.get("authenticated") is True
        return self._connected

    def health(self) -> dict:
        result = self.client.health()
        result["adapter"] = "dhan_live"
        result["symbols_configured"] = len(self.security_map)
        return result

    def snapshot(self, symbols: Iterable[str]) -> list[MarketSnapshot]:
        if not self._connected:
            if not self.connect():
                raise RuntimeError("DhanHQ authentication is not ready.")
        return self.client.snapshots(symbols, self.security_map)
