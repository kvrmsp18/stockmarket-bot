from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent.parent


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    mode: str = os.getenv("BOT_MODE", "PAPER").upper()
    live_enabled: bool = _bool("DHAN_LIVE_TRADING_ENABLED", False)
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
    reference_capital: float = _float("BOT_RESEARCH_REFERENCE_CAPITAL", 100000.0)
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///data/trading.db")
    dhan_base_url: str = os.getenv("DHAN_API_BASE_URL", "https://api.dhan.co")
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", os.getenv("CHATGPT_API_KEY", ""))
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", os.getenv("CLAUDE_API_KEY", ""))

    @property
    def db_path(self) -> Path:
        return ROOT / "data" / "trading.db"

    @property
    def live_mode_requested(self) -> bool:
        return self.mode == "LIVE" and self.live_enabled

    def validate(self) -> None:
        if self.mode not in {"PAPER", "LIVE"}:
            raise ValueError("BOT_MODE must be PAPER or LIVE")
        if not 0 < self.risk_per_trade_pct <= 10:
            raise ValueError("RISK_PER_TRADE_PCT must be >0 and <=10")
        if self.min_rr < 1:
            raise ValueError("MIN_RR must be >=1")
        if self.bullish_threshold != 7.0 or self.bearish_threshold != 4.0:
            # Source-derived defaults remain 7/4; configuration may change them deliberately.
            pass
        if self.live_mode_requested:
            # The actual engine applies the complete safety gate. This check only rejects malformed config.
            if not os.getenv("DHAN_CLIENT_ID") or not os.getenv("DHAN_ACCESS_TOKEN"):
                raise ValueError("LIVE requested but Dhan credentials are missing")


settings = Settings()
