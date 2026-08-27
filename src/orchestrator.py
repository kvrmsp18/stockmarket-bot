from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class OrchestrationResult:
    approved: bool
    reason: str
    candidate: Optional[Any] = None
    risk_decision: Optional[Any] = None
    heartbeat: Optional[Any] = None


class TradingOrchestrator:
    """
    Coordinates candidate construction, safety checks, risk evaluation,
    and heartbeat creation.

    Safety order:
        heartbeat
        -> candidate construction
        -> candidate existence
        -> market regime gate
        -> risk evaluation
        -> final approval
    """

    def __init__(
        self,
        candidate_engine: Callable[..., Any],
        risk_evaluator: Callable[..., Any],
        heartbeat_factory: Callable[..., Any],
    ):
        self._candidate_engine = candidate_engine
        self._risk_evaluator = risk_evaluator
        self._heartbeat_factory = heartbeat_factory

    def evaluate(
        self,
        *,
        direction: str,
        entry: float,
        stop_loss: float,
        target: float,
        confidence: float,
        liquidity_ok: bool,
        symbol: str = "UNKNOWN",
        exchange: str = "NSE",
        reason: str = "Candidate evaluation",
        market_regime_ok: bool = True,
        news_status: str = "UNKNOWN",
        holding_window: str = "INTRADAY",
        service: str = "trading-engine",
    ) -> OrchestrationResult:

        # ---------------------------------------------------------
        # 1. Create heartbeat
        # ---------------------------------------------------------
        heartbeat = self._heartbeat_factory(
            service,
            "evaluating",
        )

        # ---------------------------------------------------------
        # 2. Build candidate
        # ---------------------------------------------------------
        try:
            candidate = self._candidate_engine(
                symbol=symbol,
                exchange=exchange,
                direction=direction,
                entry=entry,
                stop_loss=stop_loss,
                target=target,
                confidence=confidence,
                reason=reason,
                liquidity_ok=liquidity_ok,
                market_regime_ok=market_regime_ok,
                news_status=news_status,
                holding_window=holding_window,
            )

        except (ValueError, TypeError) as exc:
            return OrchestrationResult(
                approved=False,
                reason=f"Candidate rejected: {exc}",
                heartbeat=heartbeat,
            )

        # ---------------------------------------------------------
        # 3. Candidate must exist
        # ---------------------------------------------------------
        if candidate is None:
            return OrchestrationResult(
                approved=False,
                reason="No valid candidate",
                heartbeat=heartbeat,
            )

        # ---------------------------------------------------------
        # 4. MARKET REGIME SAFETY GATE
        #
        # This check must happen before risk evaluation.
        # A candidate must NEVER be approved when the market
        # regime is explicitly unsafe.
        # ---------------------------------------------------------
        if not market_regime_ok:
            return OrchestrationResult(
                approved=False,
                reason="Market regime is not acceptable",
                candidate=candidate,
                heartbeat=heartbeat,
            )

        # ---------------------------------------------------------
        # 5. Risk evaluation
        # ---------------------------------------------------------
        try:
            risk_decision = self._risk_evaluator(
                direction=direction,
                entry=entry,
                stop_loss=stop_loss,
                target=target,
                confidence=confidence,
                liquidity_ok=liquidity_ok,
            )

        except (ValueError, TypeError) as exc:
            return OrchestrationResult(
                approved=False,
                reason=f"Risk evaluation rejected: {exc}",
                candidate=candidate,
                heartbeat=heartbeat,
            )

        # ---------------------------------------------------------
        # 6. Risk decision must approve
        # ---------------------------------------------------------
        if not risk_decision.approved:
            return OrchestrationResult(
                approved=False,
                reason=risk_decision.reason,
                candidate=candidate,
                risk_decision=risk_decision,
                heartbeat=heartbeat,
            )

        # ---------------------------------------------------------
        # 7. Final approval
        #
        # At this point:
        # - candidate exists
        # - market regime is acceptable
        # - risk checks passed
        # ---------------------------------------------------------
        return OrchestrationResult(
            approved=True,
            reason="Candidate passed all safety checks",
            candidate=candidate,
            risk_decision=risk_decision,
            heartbeat=heartbeat,
        )
