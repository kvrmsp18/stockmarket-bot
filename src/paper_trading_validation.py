"""Paper-trading validation for research signals.

This module is deliberately broker-independent and never places an order.
It freezes a signal at its creation time, evaluates only later market bars,
and calculates estimated all-in net P&L after common intraday charges.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from typing import Iterable, Sequence

from .research_market_data import ResearchBar
from .trading_engine import estimate_trade_economics


OUTCOMES = ("TARGET", "STOP", "EOD_CLOSE", "OPEN", "AMBIGUOUS", "NO_DATA")


@dataclass(frozen=True)
class PaperSignal:
    """A signal frozen at the moment it was generated."""

    signal_id: str
    symbol: str
    direction: str
    generated_at: datetime
    entry: float
    stop_loss: float
    target: float
    quantity: int
    confidence: float
    risk_reward: float

    def __post_init__(self) -> None:
        if not self.signal_id.strip():
            raise ValueError("signal_id is required")
        if not self.symbol.strip():
            raise ValueError("symbol is required")
        if self.direction not in {"BUY", "SELL"}:
            raise ValueError("direction must be BUY or SELL")
        if self.entry <= 0 or self.stop_loss <= 0 or self.target <= 0:
            raise ValueError("entry, stop_loss and target must be positive")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if not isfinite(self.confidence):
            raise ValueError("confidence must be finite")


@dataclass(frozen=True)
class PaperOutcome:
    """The result of evaluating a frozen signal against future bars only."""

    signal_id: str
    symbol: str
    direction: str
    generated_at: datetime
    entry: float
    stop_loss: float
    target: float
    quantity: int
    outcome: str
    exit_at: datetime | None
    exit_price: float | None
    pnl: float | None
    estimated_charges: float | None
    net_pnl: float | None
    max_favourable_move: float | None
    max_adverse_move: float | None
    bars_observed: int
    reason: str

    @property
    def gross_pnl(self) -> float | None:
        """Explicit alias for the historical ``pnl`` field."""
        return self.pnl


@dataclass(frozen=True)
class ValidationSummary:
    """Aggregated paper-trading performance for a bounded reporting period."""

    period_start: date
    period_end: date
    signals: int
    target_count: int
    stop_count: int
    eod_close_count: int
    open_count: int
    ambiguous_count: int
    no_data_count: int
    resolved_count: int
    win_rate_percent: float
    gross_profit: float
    gross_loss: float
    net_pnl: float
    profit_factor: float | None
    average_winner: float | None
    average_loser: float | None
    max_drawdown: float

    @property
    def closed_count(self) -> int:
        """All paper trades with a realized exit price."""
        return self.target_count + self.stop_count + self.eod_close_count


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _validate_bars(bars: Sequence[ResearchBar]) -> tuple[ResearchBar, ...]:
    ordered = tuple(sorted(bars, key=lambda item: _utc(item.timestamp)))
    for bar in ordered:
        if bar.high < bar.low:
            raise ValueError("Research bar high cannot be below low")
    return ordered


def _move(direction: str, entry: float, price: float) -> float:
    if direction == "BUY":
        return price - entry
    return entry - price


def _resolved_pnl(signal: PaperSignal, exit_price: float) -> tuple[float, float, float]:
    economics = estimate_trade_economics(
        signal.entry,
        exit_price,
        signal.quantity,
        direction=signal.direction,
    )
    return economics.gross_pnl, economics.charges.total, economics.net_pnl


def evaluate_signal(signal: PaperSignal, future_bars: Sequence[ResearchBar]) -> PaperOutcome:
    """Evaluate a signal using only bars strictly after its generation time.

    If both stop and target are touched within the same OHLC bar, the result is
    marked AMBIGUOUS rather than assuming a favourable intrabar order.
    """
    bars = tuple(
        bar for bar in _validate_bars(future_bars)
        if _utc(bar.timestamp) > _utc(signal.generated_at)
    )

    if not bars:
        return PaperOutcome(
            signal.signal_id, signal.symbol, signal.direction, signal.generated_at,
            signal.entry, signal.stop_loss, signal.target, signal.quantity,
            "NO_DATA", None, None, None, None, None, None, None, 0,
            "No market bars were available after signal generation.",
        )

    max_favourable = 0.0
    max_adverse = 0.0

    for bar in bars:
        favourable = max(
            _move(signal.direction, signal.entry, bar.high),
            _move(signal.direction, signal.entry, bar.low),
        )
        adverse = min(
            _move(signal.direction, signal.entry, bar.high),
            _move(signal.direction, signal.entry, bar.low),
        )
        max_favourable = max(max_favourable, favourable)
        max_adverse = min(max_adverse, adverse)

        if signal.direction == "BUY":
            hit_stop = bar.low <= signal.stop_loss
            hit_target = bar.high >= signal.target
        else:
            hit_stop = bar.high >= signal.stop_loss
            hit_target = bar.low <= signal.target

        if hit_stop and hit_target:
            return PaperOutcome(
                signal.signal_id, signal.symbol, signal.direction, signal.generated_at,
                signal.entry, signal.stop_loss, signal.target, signal.quantity,
                "AMBIGUOUS", _utc(bar.timestamp), None, None, None, None,
                max_favourable, max_adverse, len(bars),
                "Stop and target were both touched in the same OHLC bar; outcome not guessed.",
            )

        if hit_target:
            gross, charges, net = _resolved_pnl(signal, signal.target)
            return PaperOutcome(
                signal.signal_id, signal.symbol, signal.direction, signal.generated_at,
                signal.entry, signal.stop_loss, signal.target, signal.quantity,
                "TARGET", _utc(bar.timestamp), signal.target, gross, charges, net,
                max_favourable, max_adverse, len(bars), "Target reached first.",
            )

        if hit_stop:
            gross, charges, net = _resolved_pnl(signal, signal.stop_loss)
            return PaperOutcome(
                signal.signal_id, signal.symbol, signal.direction, signal.generated_at,
                signal.entry, signal.stop_loss, signal.target, signal.quantity,
                "STOP", _utc(bar.timestamp), signal.stop_loss, gross, charges, net,
                max_favourable, max_adverse, len(bars), "Stop loss reached first.",
            )

    return PaperOutcome(
        signal.signal_id, signal.symbol, signal.direction, signal.generated_at,
        signal.entry, signal.stop_loss, signal.target, signal.quantity, "OPEN",
        None, None, None, None, None, max_favourable, max_adverse, len(bars),
        "Neither target nor stop was reached in the supplied future bars.",
    )


def close_at_eod(signal: PaperSignal, future_bars: Sequence[ResearchBar]) -> PaperOutcome:
    """Close an otherwise-open paper signal at the latest available bar close.

    Target/stop resolution is always checked first. If neither level was hit,
    the latest later close becomes the deterministic EOD exit price.
    """
    evaluated = evaluate_signal(signal, future_bars)
    if evaluated.outcome != "OPEN":
        return evaluated

    bars = tuple(
        bar for bar in _validate_bars(future_bars)
        if _utc(bar.timestamp) > _utc(signal.generated_at)
    )
    if not bars:
        return evaluated

    latest = bars[-1]
    gross, charges, net = _resolved_pnl(signal, latest.close)
    return PaperOutcome(
        signal.signal_id,
        signal.symbol,
        signal.direction,
        signal.generated_at,
        signal.entry,
        signal.stop_loss,
        signal.target,
        signal.quantity,
        "EOD_CLOSE",
        _utc(latest.timestamp),
        float(latest.close),
        gross,
        charges,
        net,
        evaluated.max_favourable_move,
        evaluated.max_adverse_move,
        evaluated.bars_observed,
        "Intraday paper trade was closed at the latest available cash-market bar for EOD risk reset.",
    )


def _drawdown(pnls: Iterable[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return round(drawdown, 2)


def summarize_outcomes(
    outcomes: Sequence[PaperOutcome],
    *,
    period_start: date,
    period_end: date,
) -> ValidationSummary:
    """Build a daily or weekly summary from paper outcomes."""
    target = sum(item.outcome == "TARGET" for item in outcomes)
    stop = sum(item.outcome == "STOP" for item in outcomes)
    eod_close = sum(item.outcome == "EOD_CLOSE" for item in outcomes)
    open_count = sum(item.outcome == "OPEN" for item in outcomes)
    ambiguous = sum(item.outcome == "AMBIGUOUS" for item in outcomes)
    no_data = sum(item.outcome == "NO_DATA" for item in outcomes)
    resolved = target + stop

    realized = [
        item for item in outcomes
        if item.outcome in {"TARGET", "STOP", "EOD_CLOSE"} and item.pnl is not None
    ]
    winners = [item.pnl for item in realized if item.pnl is not None and item.pnl > 0]
    losers = [item.pnl for item in realized if item.pnl is not None and item.pnl < 0]
    gross_profit = round(sum(winners), 2)
    gross_loss = round(abs(sum(losers)), 2)

    net_pnls = [item.net_pnl for item in realized if item.net_pnl is not None]
    net_pnl = round(sum(net_pnls), 2)
    profit_factor = None if gross_loss == 0 else round(gross_profit / gross_loss, 4)

    return ValidationSummary(
        period_start=period_start,
        period_end=period_end,
        signals=len(outcomes),
        target_count=target,
        stop_count=stop,
        eod_close_count=eod_close,
        open_count=open_count,
        ambiguous_count=ambiguous,
        no_data_count=no_data,
        resolved_count=resolved,
        win_rate_percent=round((target / resolved) * 100.0, 2) if resolved else 0.0,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        net_pnl=net_pnl,
        profit_factor=profit_factor,
        average_winner=round(gross_profit / len(winners), 2) if winners else None,
        average_loser=round(gross_loss / len(losers), 2) if losers else None,
        max_drawdown=_drawdown(net_pnls),
    )


def week_bounds(day: date) -> tuple[date, date]:
    """Return Monday-Sunday bounds containing ``day``."""
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def render_validation_report(summary: ValidationSummary, *, title: str) -> str:
    """Render a compact human-readable report for the dashboard or a file."""
    factor = "N/A" if summary.profit_factor is None else f"{summary.profit_factor:.2f}"
    avg_winner = "N/A" if summary.average_winner is None else f"₹{summary.average_winner:,.2f}"
    avg_loser = "N/A" if summary.average_loser is None else f"₹{summary.average_loser:,.2f}"
    return "\n".join([
        f"# {title}",
        f"Period: {summary.period_start.isoformat()} to {summary.period_end.isoformat()}",
        "",
        "## Signal results",
        f"- Signals: {summary.signals}",
        f"- Target: {summary.target_count}",
        f"- Stop: {summary.stop_count}",
        f"- EOD close: {summary.eod_close_count}",
        f"- Open: {summary.open_count}",
        f"- Ambiguous: {summary.ambiguous_count}",
        f"- No data: {summary.no_data_count}",
        f"- Target/stop win rate: {summary.win_rate_percent:.2f}%",
        "",
        "## Paper performance",
        f"- Closed paper trades: {summary.closed_count}",
        f"- Gross profit: ₹{summary.gross_profit:,.2f}",
        f"- Gross loss: ₹{summary.gross_loss:,.2f}",
        f"- Net paper P&L after estimated charges: ₹{summary.net_pnl:,.2f}",
        f"- Profit factor: {factor}",
        f"- Average winner: {avg_winner}",
        f"- Average loser: {avg_loser}",
        f"- Max drawdown: ₹{summary.max_drawdown:,.2f}",
    ])
