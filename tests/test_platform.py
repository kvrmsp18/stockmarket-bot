from __future__ import annotations

import pandas as pd
import numpy as np

from intraday_bot.config import settings
from intraday_bot.research import source_roce, source_valuation, scrap_analysis
from intraday_bot.risk import position_size, risk_reward, risk_gate
from intraday_bot.technical import indicators, trend_state


def sample_bars(n=120):
    close=np.linspace(100,120,n)+np.sin(np.arange(n))*0.5
    return pd.DataFrame({"timestamp":pd.date_range("2026-01-01",periods=n,freq="5min",tz="UTC"),"open":close-0.2,"high":close+0.5,"low":close-0.5,"close":close,"volume":np.full(n,100000.0)})


def test_source_formulas():
    assert source_valuation(10,20)==200
    assert source_roce(20,100)==20


def test_scrap_red_flag_rejects():
    r=scrap_analysis("X",{"red_flags":["fraud"]})
    assert r.rejection_reason=="RED_FLAG"


def test_indicators_and_trend_thresholds():
    x=indicators(sample_bars())
    assert {"vwap","ema20","ema50","rsi","macd","adx","atr"}.issubset(x.columns)
    assert trend_state(7.1)=="BULLISH"
    assert trend_state(3.9)=="BEARISH"


def test_quantity_is_minimum_of_safety_limits():
    r=position_size(100,98,106,100000,available_funds=5000,liquidity_qty=20,broker_max_qty=50)
    assert r.quantity==20
    assert r.risk_safe>=r.quantity and r.funds_safe>=r.quantity


def test_paper_quantity_ignores_real_broker_cash():
    r=position_size(100,98,106,1000,available_funds=0,liquidity_qty=100,broker_max_qty=100)
    assert r.quantity == 2
    assert r.capital_required == 200


def test_rr_and_risk_gate():
    assert settings.max_trades_per_day == 2
    assert risk_reward(100,98,106)==3
    assert risk_gate(3,0,0,0,consecutive_losses=0)[0]
    assert risk_gate(2.9,0,0,0,consecutive_losses=0)[0] is False
    assert risk_gate(3,20000,0,0,consecutive_losses=0)[1]=="DAILY_LOSS_LIMIT"


def test_emergency_stop_and_consecutive_loss_gates():
    assert risk_gate(3,0,0,0,emergency_stop=True,consecutive_losses=0)[1] == "EMERGENCY_STOP"
    assert risk_gate(3,0,0,0,emergency_stop=False,consecutive_losses=settings.max_consecutive_losses)[1] == "MAX_CONSECUTIVE_LOSSES"
