"""Research dashboard models for stock monitoring and quantity control.

This module is broker-independent. It turns ranked research candidates into
monitor rows and provides safe manual quantity override validation.

It also provides a multi-stock monitor snapshot so the future UI can show the
full daily filtered universe, one chart per actionable stock, BUY/SELL status,
and an editable quantity that can never exceed configured risk/capital limits.
No function in this module places, modifies, or cancels broker orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping, Sequence

from .models import TradeCandidate
from .research_market_data import ResearchBar
from .risk_management import RiskConfig


class QuantityOverrideError(ValueError):
    """Raised when a requested manual quantity is unsafe or invalid."""


class StockMonitorError(ValueError):
    """Raised when a monitor snapshot cannot be built safely."""


@dataclass(frozen=True)
class QuantityOverrideResult:
    """Validated quantity choice for a candidate."""

    symbol: str
    direction: str
    requested_quantity: int
    approved: bool
    risk_amount: float
    capital_required: float
    risk_percent: float
    maximum_safe_quantity: int
    reason: str


@dataclass(frozen=True)
class StockMonitorRow:
    """All research information required by one dashboard stock card."""

    symbol: str
    exchange: str
    direction: str
    status: str
    price: float
    entry: float
    stop_loss: float
    target: float
    confidence: float
    risk_reward: float
    potential_percent: float
    ai_quantity: int
    selected_quantity: int
    maximum_safe_quantity: int
    risk_amount: float
    capital_required: float
    risk_percent: float
    reason: str
    chart_bars: tuple[ResearchBar, ...]


@dataclass(frozen=True)
class StockMonitorSnapshot:
    """Daily monitor state for all actionable filtered stocks.

    ``filtered_count`` is the number of unique symbols scanned that day.
    ``actionable_count`` is the number that survived the complete research,
    ranking and risk gates and therefore receive monitor cards/charts.
    """

    filtered_count: int
    scanned_count: int
    actionable_count: int
    buy_count: int
    sell_count: int
    rows: tuple[StockMonitorRow, ...]


def _validate_account_and_config(
    account_equity: float,
    config: RiskConfig,
) -> None:
    if not isfinite(account_equity) or account_equity <= 0:
        raise QuantityOverrideError("Account equity must be positive and finite.")
    if config.max_risk_per_trade_percent <= 0:
        raise QuantityOverrideError("Maximum risk per trade must be positive.")
    if config.max_capital_allocation_percent <= 0:
        raise QuantityOverrideError("Maximum capital allocation must be positive.")
    if config.max_quantity <= 0:
        raise QuantityOverrideError("Maximum quantity must be positive.")


def _quantity_limits(
    candidate: TradeCandidate,
    account_equity: float,
    config: RiskConfig,
) -> tuple[int, float, float]:
    _validate_account_and_config(account_equity, config)

    if candidate.risk_per_share <= 0:
        raise QuantityOverrideError("Candidate risk per share must be positive.")
    if candidate.entry <= 0:
        raise QuantityOverrideError("Candidate entry must be positive.")

    risk_budget = account_equity * config.max_risk_per_trade_percent / 100.0
    capital_limit = account_equity * config.max_capital_allocation_percent / 100.0

    quantity_by_risk = int(risk_budget // candidate.risk_per_share)
    quantity_by_capital = int(capital_limit // candidate.entry)
    maximum_safe_quantity = min(
        quantity_by_risk,
        quantity_by_capital,
        config.max_quantity,
    )

    return maximum_safe_quantity, risk_budget, capital_limit


def validate_quantity_override(
    candidate: TradeCandidate,
    account_equity: float,
    requested_quantity: int,
    *,
    config: RiskConfig | None = None,
) -> QuantityOverrideResult:
    """Validate a dashboard quantity override without placing an order."""
    cfg = config or RiskConfig()
    maximum_safe_quantity, risk_budget, capital_limit = _quantity_limits(
        candidate,
        account_equity,
        cfg,
    )

    if not isinstance(requested_quantity, int) or isinstance(requested_quantity, bool):
        raise QuantityOverrideError("Quantity must be an integer.")
    if requested_quantity <= 0:
        raise QuantityOverrideError("Quantity must be positive.")

    risk_amount = requested_quantity * candidate.risk_per_share
    capital_required = requested_quantity * candidate.entry
    risk_percent = (risk_amount / account_equity) * 100.0

    if requested_quantity > maximum_safe_quantity:
        return QuantityOverrideResult(
            symbol=candidate.symbol,
            direction=candidate.direction,
            requested_quantity=requested_quantity,
            approved=False,
            risk_amount=round(risk_amount, 2),
            capital_required=round(capital_required, 2),
            risk_percent=round(risk_percent, 4),
            maximum_safe_quantity=maximum_safe_quantity,
            reason=(
                f"Quantity exceeds the safe limit of {maximum_safe_quantity}; "
                f"risk budget is ₹{risk_budget:.2f} and capital limit is "
                f"₹{capital_limit:.2f}."
            ),
        )

    return QuantityOverrideResult(
        symbol=candidate.symbol,
        direction=candidate.direction,
        requested_quantity=requested_quantity,
        approved=True,
        risk_amount=round(risk_amount, 2),
        capital_required=round(capital_required, 2),
        risk_percent=round(risk_percent, 4),
        maximum_safe_quantity=maximum_safe_quantity,
        reason="Quantity is within configured risk and capital limits.",
    )


def build_monitor_row(
    candidate: TradeCandidate,
    bars: Sequence[ResearchBar],
    account_equity: float,
    *,
    config: RiskConfig | None = None,
    selected_quantity: int | None = None,
) -> StockMonitorRow:
    """Build one dashboard-ready stock row with its chart history."""
    if not bars:
        raise QuantityOverrideError("At least one chart bar is required.")

    cfg = config or RiskConfig()
    maximum_safe_quantity, _, _ = _quantity_limits(candidate, account_equity, cfg)

    ai_quantity = min(maximum_safe_quantity, cfg.max_quantity)
    if ai_quantity <= 0:
        raise QuantityOverrideError("Candidate has no safe positive quantity.")

    chosen_quantity = ai_quantity if selected_quantity is None else selected_quantity
    override = validate_quantity_override(
        candidate,
        account_equity,
        chosen_quantity,
        config=cfg,
    )

    if not override.approved:
        raise QuantityOverrideError(override.reason)

    return StockMonitorRow(
        symbol=candidate.symbol,
        exchange=candidate.exchange,
        direction=candidate.direction,
        status="ACTIONABLE",
        price=round(candidate.entry, 2),
        entry=candidate.entry,
        stop_loss=candidate.stop_loss,
        target=candidate.target,
        confidence=candidate.confidence,
        risk_reward=candidate.risk_reward,
        potential_percent=candidate.potential_percent,
        ai_quantity=ai_quantity,
        selected_quantity=override.requested_quantity,
        maximum_safe_quantity=maximum_safe_quantity,
        risk_amount=override.risk_amount,
        capital_required=override.capital_required,
        risk_percent=override.risk_percent,
        reason=candidate.reason,
        chart_bars=tuple(bars),
    )


def build_monitor_snapshot(
    scan_result: object,
    account_equity: float,
    *,
    config: RiskConfig | None = None,
    selected_quantities: Mapping[str, int] | None = None,
) -> StockMonitorSnapshot:
    """Convert a ``ResearchScanResult`` into dashboard-ready stock cards.

    The function deliberately accepts the scan result structurally instead of
    importing ``research_pipeline`` to avoid a circular dependency. Only the
    public fields produced by that module are required.

    Every actionable candidate must have its validated bars retained by the
    scanner. Missing bars are treated as an internal data-integrity error
    rather than silently rendering a chart with stale or synthetic data.
    """
    candidates = tuple(getattr(scan_result, "actionable_candidates", ()))
    results = tuple(getattr(scan_result, "results", ()))
    filtered_count = int(getattr(scan_result, "requested_count", len(results)))
    scanned_count = int(getattr(scan_result, "scanned_count", len(results)))

    if filtered_count < 0 or scanned_count < 0:
        raise StockMonitorError("Scan counts cannot be negative.")

    result_by_symbol = {
        str(getattr(result, "requested_symbol", "")).strip().upper(): result
        for result in results
    }
    quantities = {
        str(symbol).strip().upper(): quantity
        for symbol, quantity in (selected_quantities or {}).items()
    }

    rows: list[StockMonitorRow] = []
    seen: set[str] = set()

    for candidate in candidates:
        symbol = str(candidate.symbol).strip().upper()
        if not symbol or symbol in seen:
            continue

        result = result_by_symbol.get(symbol)
        if result is None:
            raise StockMonitorError(
                f"Validated scan result is missing for actionable symbol {symbol}."
            )

        bars = tuple(getattr(result, "bars", ()))
        if not bars:
            raise StockMonitorError(
                f"Validated chart bars are missing for actionable symbol {symbol}."
            )

        rows.append(
            build_monitor_row(
                candidate,
                bars,
                account_equity,
                config=config,
                selected_quantity=quantities.get(symbol),
            )
        )
        seen.add(symbol)

    buy_count = sum(1 for row in rows if row.direction == "BUY")
    sell_count = sum(1 for row in rows if row.direction == "SELL")

    return StockMonitorSnapshot(
        filtered_count=filtered_count,
        scanned_count=scanned_count,
        actionable_count=len(rows),
        buy_count=buy_count,
        sell_count=sell_count,
        rows=tuple(rows),
    )
