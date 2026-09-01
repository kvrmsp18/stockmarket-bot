from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent.parent


def _secret_or_env(name: str, default: str = "") -> str:
    """Read configuration from environment first, then Streamlit Secrets."""
    value = os.getenv(name)
    if value is not None and value.strip() != "":
        return value.strip()
    try:
        import streamlit as st
        secret_value = st.secrets.get(name, default)
        if secret_value is None:
            return default
        return str(secret_value).strip()
    except Exception:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_secret_or_env(name, str(default)))
    except (TypeError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(_secret_or_env(name, str(default)))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool = False) -> bool:
    return _secret_or_env(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    mode: str = _secret_or_env("BOT_MODE", "PAPER").upper()
    live_enabled: bool = _bool("DHAN_LIVE_TRADING_ENABLED", False)
    emergency_stop: bool = _bool("BOT_EMERGENCY_STOP", False)
    max_consecutive_losses: int = _int("MAX_CONSECUTIVE_LOSSES", 3)
    max_trades_per_day: int = _int("MAX_TRADES_PER_DAY", 2)
    risk_per_trade_pct: float = _float("RISK_PER_TRADE_PCT", 0.5)
    daily_loss_limit: float = _float("MAX_DAILY_LOSS", 15000.0)
    max_positions: int = _int("MAX_OPEN_POSITIONS", 5)
    max_position_exposure: float = _float("MAX_POSITION_EXPOSURE", 250000.0)
    max_sector_exposure: float = _float("MAX_SECTOR_EXPOSURE", 0.30)
    max_capital_deployment: float = _float("MAX_CAPITAL_DEPLOYMENT", 0.80)
    cash_reserve_pct: float = _float("CASH_RESERVE_PCT", 0.20)
    min_rr: float = _float("MIN_RR", 3.0)
    bullish_threshold: float = _float("TREND_BULLISH_THRESHOLD", 7.0)
    bearish_threshold: float = _float("TREND_BEARISH_THRESHOLD", 4.0)
    freshness_seconds: int = _int("DATA_FRESHNESS_SECONDS", 180)
    scan_workers: int = _int("SCAN_WORKERS", 8)
    cycle_budget_seconds: int = _int("CYCLE_BUDGET_SECONDS", 240)
    square_off_hour: int = _int("SQUARE_OFF_HOUR", 15)
    square_off_minute: int = _int("SQUARE_OFF_MINUTE", 20)
    reference_capital: float = _float("BOT_RESEARCH_REFERENCE_CAPITAL", 1000.0)
    database_url: str = _secret_or_env("DATABASE_URL", "sqlite:///data/trading.db")
    dhan_base_url: str = _secret_or_env("DHAN_API_BASE_URL", "https://api.dhan.co")
    dhan_client_id: str = _secret_or_env("DHAN_CLIENT_ID", "")
    dhan_access_token: str = _secret_or_env("DHAN_ACCESS_TOKEN", "")
    dhan_api_key: str = _secret_or_env("DHAN_API_KEY", "")
    dhan_security_ids_json: str = _secret_or_env("DHAN_SECURITY_IDS_JSON", "{}")
    bse_scrip_codes_json: str = _secret_or_env("BSE_SCRIP_CODES_JSON", "{}")
    telegram_token: str = _secret_or_env("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = _secret_or_env("TELEGRAM_CHAT_ID", "")
    openai_api_key: str = _secret_or_env("OPENAI_API_KEY", _secret_or_env("CHATGPT_API_KEY", ""))
    anthropic_api_key: str = _secret_or_env("ANTHROPIC_API_KEY", _secret_or_env("CLAUDE_API_KEY", ""))

    @property
    def db_path(self) -> Path:
        return ROOT / "data" / "trading.db"

    @property
    def live_mode_requested(self) -> bool:
        return self.mode == "LIVE" and self.live_enabled and not self.emergency_stop

    def validate(self) -> None:
        if self.mode not in {"PAPER", "LIVE"}:
            raise ValueError("BOT_MODE must be PAPER or LIVE")
        if not 0 < self.risk_per_trade_pct <= 10:
            raise ValueError("RISK_PER_TRADE_PCT must be >0 and <=10")
        if self.min_rr < 1:
            raise ValueError("MIN_RR must be >=1")
        if self.max_consecutive_losses < 1:
            raise ValueError("MAX_CONSECUTIVE_LOSSES must be >=1")
        if self.max_trades_per_day < 1:
            raise ValueError("MAX_TRADES_PER_DAY must be >=1")
        if self.reference_capital <= 0:
            raise ValueError("BOT_RESEARCH_REFERENCE_CAPITAL must be >0")
        if self.live_mode_requested:
            if not self.dhan_client_id or not self.dhan_access_token:
                raise ValueError("LIVE requested but Dhan credentials are missing")


settings = Settings()
