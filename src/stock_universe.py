"""Bot-managed default stock universe for unattended research scans.

The dashboard deliberately does not ask the user to choose stocks. This module
owns the default NSE liquid-equity universe used by the one-click monitor.
The research pipeline still decides which symbols become actionable.

The universe is maintained against current listed trading symbols. Corporate
renames/demergers are represented by their currently listed symbols rather than
legacy tickers, because the Dhan instrument master is the source of truth for
intraday security IDs.
"""

from __future__ import annotations

# A broad, liquid large-cap NSE universe. This is an internal scan universe,
# not a recommendation list. The bot researches every symbol and applies its
# existing data-quality, technical, ranking, and risk gates before displaying
# any candidate.
DEFAULT_NSE_RESEARCH_UNIVERSE: tuple[str, ...] = (
    "RELIANCE",
    "TCS",
    "HDFCBANK",
    "ICICIBANK",
    "INFY",
    "ITC",
    "SBIN",
    "BHARTIARTL",
    "LT",
    "AXISBANK",
    "KOTAKBANK",
    "HINDUNILVR",
    "BAJFINANCE",
    "MARUTI",
    "SUNPHARMA",
    "M&M",
    "HCLTECH",
    "TITAN",
    "ULTRACEMCO",
    "ADANIENT",
    "NTPC",
    "TMPV",
    "TMCV",
    "ONGC",
    "POWERGRID",
    "ADANIPORTS",
    "COALINDIA",
    "TATASTEEL",
    "JSWSTEEL",
    "BAJAJFINSV",
    "WIPRO",
    "NESTLEIND",
    "ASIANPAINT",
    "TECHM",
    "HINDALCO",
    "GRASIM",
    "EICHERMOT",
    "HEROMOTOCO",
    "DRREDDY",
    "CIPLA",
    "DIVISLAB",
    "APOLLOHOSP",
    "BRITANNIA",
    "BPCL",
    "TATACONSUM",
    "SBILIFE",
    "HDFCLIFE",
    "SHRIRAMFIN",
    "BAJAJ-AUTO",
    "TRENT",
    "BEL",
    "ETERNAL",
)


def get_default_research_universe(exchange: str = "NSE") -> tuple[str, ...]:
    """Return the bot-managed default research universe.

    The dashboard currently runs the unattended research workflow on NSE.
    BSE support remains available in the underlying pipeline but is not exposed
    as a required user choice in the one-click dashboard.
    """
    normalized = exchange.strip().upper()
    if normalized != "NSE":
        raise ValueError("The unattended dashboard universe currently supports NSE only.")
    return DEFAULT_NSE_RESEARCH_UNIVERSE
