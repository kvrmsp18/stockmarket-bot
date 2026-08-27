"""End-to-end strategy validation using the research pipeline.

Research only: no broker access and no order placement. The strategy is
re-evaluated using only bars available before each simulated entry.

Backtests intentionally disable the live Dhan MTF gate. Historical backtest
inputs already define the supplied test universe; the production monitor uses
Dhan's dedicated 900-day history for monthly/weekly/daily RSI confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .backtesting import BacktestReport, run_backtest
from .candidate_engine import CandidateConfig, generate_candidate
from .research_market_data import ResearchBar
from .technical_analysis import analyze


@dataclass(frozen=True)
class StrategyConfig:
    minimum_history_bars: int = 60
    candidate: CandidateConfig = CandidateConfig(require_mtf_rsi_confirmation=False)
    initial_capital: float = 100000.0
    quantity: int = 1


def _strategy(history: Sequence[ResearchBar], config: StrategyConfig):
    if len(history) < config.minimum_history_bars:
        return None
    snapshot = analyze(tuple(history))
    return generate_candidate(snapshot, exchange="NSE", config=config.candidate)


def backtest_strategy(
    bars: Sequence[ResearchBar],
    *,
    config: StrategyConfig | None = None,
) -> BacktestReport:
    """Run the current deterministic strategy against supplied history."""
    cfg = config or StrategyConfig()
    return run_backtest(
        bars,
        lambda history: _strategy(history, cfg),
        initial_capital=cfg.initial_capital,
        quantity=cfg.quantity,
    )
