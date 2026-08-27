"""Safe live market-data service built on the DhanHQ adapter.

This module is intentionally read-only. It fetches live LTP snapshots and
performs basic data-quality checks, but it has no order-placement capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from .dhan_api import DhanHQClient, load_security_map_from_env
from .models import MarketSnapshot


class MarketDataError(RuntimeError):
    """Raised when live market data cannot be safely consumed."""


@dataclass(frozen=True)
class MarketDataResult:
    """Read-only result returned by a market-data poll."""

    snapshots: tuple[MarketSnapshot, ...]
    received_at: datetime


class DhanMarketDataService:
    """Read-only service for live NSE/BSE snapshots through DhanHQ."""

    def __init__(
        self,
        client: DhanHQClient,
        security_map: dict[str, dict[str, int]] | None = None,
    ) -> None:
        self.client = client
        self.security_map = security_map if security_map is not None else load_security_map_from_env()

    def configured_symbols(self) -> tuple[str, ...]:
        """Return configured symbols in deterministic order."""
        return tuple(sorted(self.security_map))

    def poll(self, symbols: Iterable[str] | None = None) -> MarketDataResult:
        """Fetch and validate one read-only live market-data snapshot."""
        requested = tuple(symbols) if symbols is not None else self.configured_symbols()
        if not requested:
            raise MarketDataError(
                "No instruments configured. Add official Dhan exchange/security IDs first."
            )

        try:
            snapshots = tuple(self.client.snapshots(requested, self.security_map))
        except (KeyError, ValueError, RuntimeError) as exc:
            raise MarketDataError(str(exc)) from exc

        if len(snapshots) != len(requested):
            raise MarketDataError(
                f"Expected {len(requested)} market snapshots but received {len(snapshots)}."
            )

        for snapshot in snapshots:
            self._validate_snapshot(snapshot)

        return MarketDataResult(
            snapshots=snapshots,
            received_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _validate_snapshot(snapshot: MarketSnapshot) -> None:
        if not snapshot.symbol.strip():
            raise MarketDataError("Market snapshot has an empty symbol.")
        if snapshot.exchange not in {"NSE_EQ", "BSE_EQ", "NSE_FNO", "BSE_FNO", "NSE_CURRENCY", "BSE_CURRENCY", "MCX_COMM"}:
            raise MarketDataError(
                f"Unsupported/unknown Dhan exchange in snapshot: {snapshot.exchange}"
            )
        if snapshot.last_price <= 0:
            raise MarketDataError(
                f"Invalid non-positive LTP for {snapshot.symbol}: {snapshot.last_price}"
            )
        if snapshot.previous_close is not None and snapshot.previous_close <= 0:
            raise MarketDataError(
                f"Invalid previous close for {snapshot.symbol}: {snapshot.previous_close}"
            )
        if snapshot.volume is not None and snapshot.volume < 0:
            raise MarketDataError(
                f"Invalid negative volume for {snapshot.symbol}: {snapshot.volume}"
            )
        if snapshot.bid is not None and snapshot.bid < 0:
            raise MarketDataError(
                f"Invalid bid for {snapshot.symbol}: {snapshot.bid}"
            )
        if snapshot.ask is not None and snapshot.ask < 0:
            raise MarketDataError(
                f"Invalid ask for {snapshot.symbol}: {snapshot.ask}"
            )
