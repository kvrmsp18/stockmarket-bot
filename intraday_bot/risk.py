from __future__ import annotations

from dataclasses import dataclass
from math import floor

from .config import settings


@dataclass(frozen=True)
class SizeResult:
    risk_safe: int
    funds_safe: int
    position_safe: int
    liquidity_safe: int
    broker_safe: int
    quantity: int
    capital_required: float
    max_risk: float
    potential_reward: float
    rr: float
    reason: str | None = None


def position_size(
    entry: float,
    stop: float,
    target: float,
    capital: float,
    available_funds: float | None = None,
    liquidity_qty: int | None = None,
    broker_max_qty: int | None = None,
) -> SizeResult:
    """Calculate quantity using independent risk/funds/position/liquidity gates.

    PAPER mode uses the supplied virtual capital as its funds ceiling and
    never depends on the real broker cash balance. LIVE mode may use the
    broker-reported available funds.
    """
    if entry <= 0 or stop <= 0 or target <= 0 or capital <= 0:
        return SizeResult(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, "INVALID_INPUT")

    risk_per_share = abs(entry - stop)
    reward_per_share = abs(target - entry)
    if risk_per_share <= 0:
        return SizeResult(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, "INVALID_STOP")

    max_risk = capital * settings.risk_per_trade_pct / 100.0
    risk_qty = floor(max_risk / risk_per_share)

    if settings.mode == "PAPER":
        # Paper capital is virtual and must not be reduced by the real Dhan
        # account balance. The caller may still pass broker funds, but they
        # are intentionally ignored for the PAPER funds gate.
        funds_base = capital
    elif available_funds is None:
        funds_base = capital
    else:
        funds_base = max(0.0, available_funds)

    funds = funds_base * (1 - settings.cash_reserve_pct)
    funds_qty = floor(funds / entry)
    position_qty = floor(settings.max_position_exposure / entry)
    liquidity = liquidity_qty if liquidity_qty is not None else risk_qty
    broker = broker_max_qty if broker_max_qty is not None else risk_qty
    qty = max(0, min(risk_qty, funds_qty, position_qty, liquidity, broker))

    return SizeResult(
        risk_qty,
        funds_qty,
        position_qty,
        liquidity,
        broker,
        qty,
        qty * entry,
        qty * risk_per_share,
        qty * reward_per_share,
        reward_per_share / risk_per_share,
        None if qty else "INSUFFICIENT_FUNDS_OR_RISK",
    )


def risk_reward(entry: float, stop: float, target: float) -> float:
    risk = abs(entry - stop)
    return abs(target - entry) / risk if risk else 0.0


def risk_gate(
    rr: float,
    daily_loss: float,
    open_positions: int,
    sector_exposure: float,
    emergency_stop: bool = False,
) -> tuple[bool, str | None]:
    if emergency_stop:
        return False, "EMERGENCY_STOP"
    if daily_loss >= settings.daily_loss_limit:
        return False, "DAILY_LOSS_LIMIT"
    if open_positions >= settings.max_positions:
        return False, "POSITION_LIMIT"
    if sector_exposure >= settings.max_sector_exposure:
        return False, "SECTOR_EXPOSURE_LIMIT"
    if rr < settings.min_rr:
        return False, "RISK_REJECTION"
    return True, None
