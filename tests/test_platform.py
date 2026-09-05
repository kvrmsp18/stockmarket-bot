from __future__ import annotations

import numpy as np
import pandas as pd

from intraday_bot.config import settings
from intraday_bot.research import source_roce, source_valuation, scrap_analysis
from intraday_bot.risk import position_size, risk_reward, risk_gate
from intraday_bot.scrap_portfolio import scrap_portfolio_exposure_check
from intraday_bot.technical import indicators, trend_state


def sample_bars(n=120):
    close = np.linspace(100, 120, n) + np.sin(np.arange(n)) * 0.5
    return pd.DataFrame({"timestamp": pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC"), "open": close - 0.2, "high": close + 0.5, "low": close - 0.5, "close": close, "volume": np.full(n, 100000.0)})


def test_source_formulas():
    assert source_valuation(10, 20) == 200
    assert source_roce(20, 100) == 20


def test_scrap_red_flag_rejects():
    r = scrap_analysis("X", {"red_flags": ["fraud"]})
    assert r.rejection_reason == "RED_FLAG"


def test_indicators_and_trend_thresholds():
    x = indicators(sample_bars())
    assert {"vwap", "ema20", "ema50", "rsi", "macd", "adx", "atr"}.issubset(x.columns)
    assert trend_state(7.1) == "BULLISH"
    assert trend_state(3.9) == "BEARISH"


def test_quantity_is_minimum_of_safety_limits():
    r = position_size(100, 98, 106, 100000, available_funds=5000, liquidity_qty=20, broker_max_qty=50)
    assert r.quantity == 8
    assert r.risk_safe >= r.quantity and r.funds_safe >= r.quantity and r.position_safe >= r.quantity


def test_paper_quantity_ignores_real_broker_cash():
    r = position_size(100, 98, 106, 1000, available_funds=0, liquidity_qty=100, broker_max_qty=100)
    assert r.quantity == 2
    assert r.capital_required == 200


def test_paper_trading_has_no_daily_trade_count_cap():
    assert settings.max_trades_per_day is None


def test_rr_and_risk_gate():
    assert settings.daily_loss_limit == 20.0
    assert settings.max_position_exposure == 800.0
    assert risk_reward(100, 98, 106) == 3
    assert risk_gate(3, 0, 0, 0, consecutive_losses=0)[0]
    assert risk_gate(2.9, 0, 0, 0, consecutive_losses=0)[0] is False
    assert risk_gate(3, 20000, 0, 0, consecutive_losses=0)[1] == "DAILY_LOSS_LIMIT"


def test_emergency_stop_and_consecutive_loss_gates():
    assert risk_gate(3, 0, 0, 0, emergency_stop=True, consecutive_losses=0)[1] == "EMERGENCY_STOP"
    assert risk_gate(3, 0, 0, 0, emergency_stop=False, consecutive_losses=settings.max_consecutive_losses)[1] == "MAX_CONSECUTIVE_LOSSES"


def test_scrap_sector_boundary_and_projection():
    positions = [{"symbol": "A", "sector": "IT", "quantity": 1, "current_price": 150, "entry_price": 150}]
    exact = scrap_portfolio_exposure_check("B", "IT", 0, 1000, positions)
    assert exact["allowed"] is True
    assert exact["sector_weight_pct"] == 15.0

    reject = scrap_portfolio_exposure_check("B", "IT", 1, 1000, positions)
    assert reject["allowed"] is False
    assert reject["reason"] == "SCRAP_REJECTION_SECTOR_EXPOSURE"
    assert reject["projected_sector_weight_pct"] == 15.1


def test_scrap_company_boundary_and_projection():
    positions = [{"symbol": "ABC", "sector": "FINANCIAL", "quantity": 1, "current_price": 250, "entry_price": 250}]
    exact = scrap_portfolio_exposure_check("ABC", "IT", 0, 1000, positions)
    assert exact["allowed"] is True
    assert exact["company_weight_pct"] == 25.0

    reject = scrap_portfolio_exposure_check("ABC", "IT", 1, 1000, positions)
    assert reject["allowed"] is False
    assert reject["reason"] == "SCRAP_REJECTION_COMPANY_EXPOSURE"
    assert reject["projected_company_weight_pct"] == 25.1


def test_scrap_empty_context_does_not_fabricate_rejection():
    result = scrap_portfolio_exposure_check("ABC", "IT", 100, 1000, None)
    assert result["allowed"] is True
    assert result["projected_company_weight_pct"] == 10.0
    assert result["projected_sector_weight_pct"] == 10.0


def test_scrap_manual_real_holdings_are_not_part_of_paper_context():
    # The paper execution layer filters positions by mode before this helper is
    # called. An actual/manual Dhan holding therefore cannot consume paper capital.
    paper_positions: list[dict] = []
    result = scrap_portfolio_exposure_check("MANUAL1", "IT", 100, 1000, paper_positions)
    assert result["projected_sector_weight_pct"] == 10.0
    assert result["projected_company_weight_pct"] == 10.0
    assert result["allowed"] is True
