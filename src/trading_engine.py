"""Broker-funds, quantity, and P&L safety calculations.

This module never places an order. It answers three questions safely:
1. How much cash is actually available at the broker?
2. What quantity can be traded without exceeding the available cash/risk limits?
3. What is the estimated gross and net P&L after common Indian equity-intraday charges?

The charge rates are configurable through environment variables because broker,
exchange, tax, and regulatory charges can change over time. Final realized P&L
should be reconciled against the broker's executed trade/ledger data.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from math import floor, isfinite


@dataclass(frozen=True)
class ChargeEstimate:
    brokerage: float
    stt: float
    exchange_transaction: float
    sebi: float
    stamp_duty: float
    gst: float
    total: float


@dataclass(frozen=True)
class TradeEconomics:
    quantity: int
    entry: float
    exit: float
    gross_pnl: float
    charges: ChargeEstimate
    net_pnl: float


@dataclass(frozen=True)
class QuantityDecision:
    recommended_quantity: int
    risk_limited_quantity: int
    cash_limited_quantity: int
    final_quantity: int
    available_funds: float
    reserve_amount: float
    estimated_entry_cost: float
    reason: str


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric.") from exc
    if value < 0 or not isfinite(value):
        raise ValueError(f"{name} must be a finite non-negative number.")
    return value


def charge_rates() -> dict[str, float]:
    """Return configurable equity-intraday charge rates.

    Defaults are conservative estimates for NSE equity intraday. They are not
    a substitute for the broker contract note; all values can be overridden in
    the local environment without changing code.
    """
    return {
        "brokerage_per_order": _env_float("DHAN_BROKERAGE_PER_ORDER", 20.0),
        "brokerage_percent": _env_float("DHAN_BROKERAGE_PERCENT", 0.0003),
        "stt_sell_percent": _env_float("DHAN_STT_SELL_PERCENT", 0.00025),
        "exchange_transaction_percent": _env_float(
            "DHAN_EXCHANGE_TRANSACTION_PERCENT", 0.0000307
        ),
        "sebi_per_crore": _env_float("DHAN_SEBI_PER_CRORE", 10.0),
        "stamp_buy_percent": _env_float("DHAN_STAMP_BUY_PERCENT", 0.00003),
        "gst_percent": _env_float("DHAN_GST_PERCENT", 0.18),
    }


def estimate_charges(
    buy_value: float,
    sell_value: float,
    *,
    rates: dict[str, float] | None = None,
) -> ChargeEstimate:
    """Estimate round-trip charges for one equity-intraday trade."""
    if buy_value < 0 or sell_value < 0:
        raise ValueError("Turnover values cannot be negative.")

    r = rates or charge_rates()
    turnover = buy_value + sell_value
    brokerage = min(
        r["brokerage_per_order"],
        buy_value * r["brokerage_percent"],
    ) + min(
        r["brokerage_per_order"],
        sell_value * r["brokerage_percent"],
    )
    stt = sell_value * r["stt_sell_percent"]
    exchange_transaction = turnover * r["exchange_transaction_percent"]
    sebi = turnover * (r["sebi_per_crore"] / 10_000_000.0)
    stamp_duty = buy_value * r["stamp_buy_percent"]
    taxable = brokerage + exchange_transaction + sebi
    gst = taxable * r["gst_percent"]
    total = brokerage + stt + exchange_transaction + sebi + stamp_duty + gst

    return ChargeEstimate(
        brokerage=round(brokerage, 2),
        stt=round(stt, 2),
        exchange_transaction=round(exchange_transaction, 2),
        sebi=round(sebi, 2),
        stamp_duty=round(stamp_duty, 2),
        gst=round(gst, 2),
        total=round(total, 2),
    )


def estimate_trade_economics(
    entry: float,
    exit: float,
    quantity: int,
    *,
    direction: str = "BUY",
) -> TradeEconomics:
    """Calculate gross and estimated net P&L for an intraday round trip."""
    if entry <= 0 or exit <= 0:
        raise ValueError("Entry and exit prices must be positive.")
    if quantity <= 0:
        raise ValueError("Quantity must be positive.")

    direction = direction.upper()
    if direction == "BUY":
        buy_value = entry * quantity
        sell_value = exit * quantity
        gross = (exit - entry) * quantity
    elif direction == "SELL":
        sell_value = entry * quantity
        buy_value = exit * quantity
        gross = (entry - exit) * quantity
    else:
        raise ValueError("Direction must be BUY or SELL.")

    charges = estimate_charges(buy_value, sell_value)
    return TradeEconomics(
        quantity=quantity,
        entry=entry,
        exit=exit,
        gross_pnl=round(gross, 2),
        charges=charges,
        net_pnl=round(gross - charges.total, 2),
    )


def _entry_cost_per_share(price: float, direction: str) -> float:
    """Conservative cash estimate for opening one intraday share."""
    if direction.upper() not in {"BUY", "SELL"}:
        raise ValueError("Direction must be BUY or SELL.")

    # For SELL/MIS, Dhan may require less margin than the full notional.
    # We intentionally use full notional here when broker margin data is not
    # available, so the safety gate never overstates affordability.
    buy_value = price
    sell_value = price
    charges = estimate_charges(buy_value, sell_value)
    return price + charges.total


def choose_quantity(
    *,
    recommended_quantity: int,
    risk_limited_quantity: int,
    available_funds: float,
    entry_price: float,
    direction: str,
    reserve_percent: float = 10.0,
) -> QuantityDecision:
    """Choose the final executable quantity from risk and available funds.

    The strategy quantity is never blindly trusted. The final quantity is the
    minimum of strategy quantity, risk-safe quantity, and the quantity that can
    be afforded after retaining a configurable cash reserve.
    """
    if recommended_quantity < 0 or risk_limited_quantity < 0:
        raise ValueError("Quantities cannot be negative.")
    if available_funds < 0 or not isfinite(available_funds):
        raise ValueError("Available funds must be finite and non-negative.")
    if entry_price <= 0:
        raise ValueError("Entry price must be positive.")
    if not 0 <= reserve_percent < 100:
        raise ValueError("Reserve percentage must be between 0 and 100.")

    reserve_amount = available_funds * reserve_percent / 100.0
    deployable = max(0.0, available_funds - reserve_amount)
    per_share = _entry_cost_per_share(entry_price, direction)
    cash_limited_quantity = int(floor(deployable / per_share))
    final_quantity = min(
        int(recommended_quantity),
        int(risk_limited_quantity),
        cash_limited_quantity,
    )

    estimated_entry_cost = round(final_quantity * per_share, 2)

    if final_quantity <= 0:
        if available_funds <= 0:
            reason = "No broker funds are available; research continues but execution is blocked."
        elif risk_limited_quantity <= 0:
            reason = "Risk rules allow no positive quantity; execution is blocked."
        else:
            reason = "Available funds are insufficient for a safe positive quantity."
    elif final_quantity < recommended_quantity:
        reason = (
            f"Reduced from {recommended_quantity} to {final_quantity} to respect "
            "risk, available funds and the cash reserve."
        )
    else:
        reason = "Full recommended quantity is affordable within the risk and cash limits."

    return QuantityDecision(
        recommended_quantity=int(recommended_quantity),
        risk_limited_quantity=int(risk_limited_quantity),
        cash_limited_quantity=int(cash_limited_quantity),
        final_quantity=int(final_quantity),
        available_funds=round(available_funds, 2),
        reserve_amount=round(reserve_amount, 2),
        estimated_entry_cost=estimated_entry_cost,
        reason=reason,
    )
