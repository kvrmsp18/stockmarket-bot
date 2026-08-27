"""Risk management and position sizing for research-stage candidates.

This module is deliberately independent of any broker. It calculates a
conservative quantity from account equity and maximum per-trade risk, then
applies configurable hard safety limits. It never places or modifies orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import floor

from .models import TradeCandidate


class RiskManagementError(ValueError):
    """Raised when risk inputs are invalid."""


@dataclass(frozen=True)
class RiskConfig:
    max_risk_per_trade_percent: float = 1.0
    # The default position-sizing contract is risk-budget driven.
    # A stricter capital-allocation ceiling can be supplied explicitly.
    max_capital_allocation_percent: float = 100.0
    minimum_risk_reward: float = 1.5
    minimum_confidence: float = 0.70
    max_quantity: int = 1000


@dataclass(frozen=True)
class RiskAssessment:
    symbol: str
    approved: bool
    quantity: int
    entry: float
    stop_loss: float
    target: float
    risk_amount: float
    capital_required: float
    risk_percent: float
    risk_reward: float
    confidence: float
    reasons: tuple[str, ...]


def assess_candidate(
    candidate: TradeCandidate,
    account_equity: float,
    *,
    config: RiskConfig | None = None,
) -> RiskAssessment:
    """Assess a candidate without sending any order to a broker."""
    cfg = config or RiskConfig()

    if account_equity <= 0:
        raise RiskManagementError("Account equity must be positive.")
    if cfg.max_risk_per_trade_percent <= 0:
        raise RiskManagementError("Maximum risk per trade must be positive.")
    if cfg.max_capital_allocation_percent <= 0:
        raise RiskManagementError("Maximum capital allocation must be positive.")
    if cfg.max_quantity <= 0:
        raise RiskManagementError("Maximum quantity must be positive.")

    reasons: list[str] = []

    risk_budget = (
        account_equity
        * cfg.max_risk_per_trade_percent
        / 100.0
    )

    capital_limit = (
        account_equity
        * cfg.max_capital_allocation_percent
        / 100.0
    )

    if candidate.confidence < cfg.minimum_confidence:
        reasons.append("confidence_below_threshold")

    if candidate.risk_reward < cfg.minimum_risk_reward:
        reasons.append("risk_reward_below_threshold")

    if not candidate.liquidity_ok:
        reasons.append("liquidity_check_failed")

    if not candidate.market_regime_ok:
        reasons.append("market_regime_check_failed")

    if candidate.risk_per_share <= 0:
        reasons.append("invalid_per_share_risk")

    if candidate.entry <= 0:
        reasons.append("invalid_entry")

    if reasons:
        return RiskAssessment(
            symbol=candidate.symbol,
            approved=False,
            quantity=0,
            entry=candidate.entry,
            stop_loss=candidate.stop_loss,
            target=candidate.target,
            risk_amount=0.0,
            capital_required=0.0,
            risk_percent=0.0,
            risk_reward=candidate.risk_reward,
            confidence=candidate.confidence,
            reasons=tuple(reasons),
        )

    quantity_by_risk = floor(
        risk_budget / candidate.risk_per_share
    )

    quantity_by_capital = floor(
        capital_limit / candidate.entry
    )

    quantity = min(
        quantity_by_risk,
        quantity_by_capital,
        cfg.max_quantity,
    )

    if quantity <= 0:
        reasons.append("insufficient_position_size")
        return RiskAssessment(
            symbol=candidate.symbol,
            approved=False,
            quantity=0,
            entry=candidate.entry,
            stop_loss=candidate.stop_loss,
            target=candidate.target,
            risk_amount=0.0,
            capital_required=0.0,
            risk_percent=0.0,
            risk_reward=candidate.risk_reward,
            confidence=candidate.confidence,
            reasons=tuple(reasons),
        )

    risk_amount = quantity * candidate.risk_per_share
    capital_required = quantity * candidate.entry
    risk_percent = (
        risk_amount / account_equity
    ) * 100.0

    return RiskAssessment(
        symbol=candidate.symbol,
        approved=True,
        quantity=quantity,
        entry=candidate.entry,
        stop_loss=candidate.stop_loss,
        target=candidate.target,
        risk_amount=round(risk_amount, 2),
        capital_required=round(capital_required, 2),
        risk_percent=round(risk_percent, 4),
        risk_reward=round(candidate.risk_reward, 2),
        confidence=round(candidate.confidence, 2),
        reasons=(),
    )
