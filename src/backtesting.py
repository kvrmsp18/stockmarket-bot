"""Deterministic historical backtesting for research-stage candidates.

The engine is intentionally broker-independent and never places orders. It
uses completed OHLCV bars and a supplied strategy function so that strategy
logic can be tested without live market access.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from .models import TradeCandidate
from .research_market_data import ResearchBar


class BacktestError(ValueError):
    """Raised when backtest inputs are invalid."""


@dataclass(frozen=True)
class BacktestTrade:
    symbol: str
    direction: str
    entry_timestamp: object
    exit_timestamp: object
    entry: float
    exit: float
    quantity: int
    pnl: float
    result: str


@dataclass(frozen=True)
class BacktestReport:
    symbol: str
    initial_capital: float
    ending_capital: float
    net_profit: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    gross_profit: float
    gross_loss: float
    profit_factor: float | None
    max_drawdown: float
    max_drawdown_percent: float
    trades: tuple[BacktestTrade, ...]


def _validate_bars(bars: Sequence[ResearchBar]) -> None:
    if len(bars) < 2:
        raise BacktestError("At least two chronological bars are required.")

    for previous, current in zip(bars[:-1], bars[1:]):
        if current.timestamp <= previous.timestamp:
            raise BacktestError("Bars must be strictly chronological.")

        if (
            current.high < current.low
            or current.close <= 0
            or current.open <= 0
            or current.high <= 0
            or current.low <= 0
        ):
            raise BacktestError("Invalid OHLCV bar supplied to backtest.")


def run_backtest(
    bars: Sequence[ResearchBar],
    strategy: Callable[[Sequence[ResearchBar]], TradeCandidate | None],
    *,
    initial_capital: float = 100000.0,
    quantity: int = 1,
) -> BacktestReport:
    """Run a deterministic next-bar-entry stop/target backtest.

    The strategy receives only bars available before the entry decision. A
    candidate generated from history ending at index ``i`` is entered at the
    next bar's open (index ``i + 1``).

    The entry bar is treated as the execution bar and is not used for stop /
    target resolution. This avoids pretending to know the intrabar sequence
    when both the stop and target may be touched inside the same OHLC bar.
    Exit evaluation begins with the first fully subsequent bar.

    If neither stop nor target is touched before the dataset ends, the
    position exits at the final close.
    """
    _validate_bars(bars)

    if initial_capital <= 0:
        raise BacktestError("Initial capital must be positive.")

    if quantity <= 0:
        raise BacktestError("Quantity must be positive.")

    capital = float(initial_capital)
    peak = capital
    max_drawdown = 0.0
    trades: list[BacktestTrade] = []
    index = 0

    while index < len(bars) - 1:
        history = bars[: index + 1]
        candidate = strategy(history)

        if candidate is None:
            index += 1
            continue

        entry_index = index + 1
        entry_bar = bars[entry_index]
        entry = entry_bar.open

        if candidate.direction in {"BUY", "LONG"}:
            direction = "LONG"
        elif candidate.direction in {"SELL", "SHORT"}:
            direction = "SHORT"
        else:
            raise BacktestError(
                f"Unsupported candidate direction: {candidate.direction}"
            )

        if direction == "LONG":
            stop = entry - candidate.risk_per_share
            target = entry + candidate.potential_per_share
        else:
            stop = entry + candidate.risk_per_share
            target = entry - candidate.potential_per_share

        exit_index = len(bars) - 1
        exit_price = bars[-1].close
        result = "TIME_EXIT"

        # Do not evaluate the execution bar. Its OHLC range does not tell us
        # whether the stop or target was reached first after the entry.
        first_exit_bar = entry_index + 1

        for future_index in range(
            first_exit_bar,
            len(bars),
        ):
            bar = bars[future_index]

            if direction == "LONG":
                stop_hit = bar.low <= stop
                target_hit = bar.high >= target
            else:
                stop_hit = bar.high >= stop
                target_hit = bar.low <= target

            # Conservative convention for later bars: when both are touched
            # in one completed bar, assume the stop was hit first because the
            # exact intrabar order is unknowable from OHLC data alone.
            if stop_hit:
                exit_index = future_index
                exit_price = stop
                result = "LOSS"
                break

            if target_hit:
                exit_index = future_index
                exit_price = target
                result = "WIN"
                break

        if direction == "LONG":
            pnl = (exit_price - entry) * quantity
        else:
            pnl = (entry - exit_price) * quantity

        capital += pnl
        peak = max(peak, capital)
        drawdown = peak - capital
        max_drawdown = max(max_drawdown, drawdown)

        if result == "TIME_EXIT":
            if pnl > 0:
                result = "WIN"
            elif pnl < 0:
                result = "LOSS"
            else:
                result = "FLAT"

        trades.append(
            BacktestTrade(
                symbol=candidate.symbol,
                direction=direction,
                entry_timestamp=entry_bar.timestamp,
                exit_timestamp=bars[exit_index].timestamp,
                entry=round(entry, 4),
                exit=round(exit_price, 4),
                quantity=quantity,
                pnl=round(pnl, 2),
                result=result,
            )
        )

        index = max(exit_index, index + 1)

    winning = sum(
        1 for trade in trades if trade.pnl > 0
    )
    losing = sum(
        1 for trade in trades if trade.pnl < 0
    )

    gross_profit = sum(
        trade.pnl for trade in trades if trade.pnl > 0
    )
    gross_loss = abs(
        sum(trade.pnl for trade in trades if trade.pnl < 0)
    )

    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = None

    max_drawdown_percent = (
        max_drawdown / initial_capital
    ) * 100.0

    return BacktestReport(
        symbol=bars[-1].symbol,
        initial_capital=round(initial_capital, 2),
        ending_capital=round(capital, 2),
        net_profit=round(capital - initial_capital, 2),
        total_trades=len(trades),
        winning_trades=winning,
        losing_trades=losing,
        win_rate=(
            round((winning / len(trades)) * 100.0, 2)
            if trades
            else 0.0
        ),
        gross_profit=round(gross_profit, 2),
        gross_loss=round(gross_loss, 2),
        profit_factor=(
            round(profit_factor, 4)
            if profit_factor is not None
            and profit_factor != float("inf")
            else profit_factor
        ),
        max_drawdown=round(max_drawdown, 2),
        max_drawdown_percent=round(max_drawdown_percent, 4),
        trades=tuple(trades),
    )
