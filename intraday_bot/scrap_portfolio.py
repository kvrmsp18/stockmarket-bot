from __future__ import annotations

from collections.abc import Iterable
from typing import Any


SCRAP_SECTOR_LIMIT_PCT = 15.0
SCRAP_COMPANY_LIMIT_PCT = 25.0


def _position_notional(position: dict[str, Any]) -> float:
    try:
        quantity = float(position.get("quantity") or 0)
        price = float(position.get("current_price") or position.get("entry_price") or 0)
        return max(0.0, quantity * price)
    except (TypeError, ValueError):
        return 0.0


def scrap_portfolio_exposure_check(
    symbol: str,
    sector: str,
    candidate_notional: float,
    reference_capital: float,
    positions: Iterable[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Apply SOURCE_RULES SCRAP portfolio limits to real simulated state.

    Portfolio weights are calculated from open PAPER/LIVE_TEST position state,
    never from fundamentals. Existing positions are valued at current_price when
    available, otherwise entry_price. The candidate's actual entry notional is
    included in projected company and sector exposure before the fill.

    Boundary semantics intentionally follow SOURCE_RULES: exposure strictly
    greater than 15% for a sector or 25% for a company is rejected. Exactly 15%
    and exactly 25% remain eligible.
    """
    symbol = str(symbol or "").strip().upper()
    sector = str(sector or "UNKNOWN").strip().upper() or "UNKNOWN"
    capital = float(reference_capital or 0)
    candidate = max(0.0, float(candidate_notional or 0))

    if capital <= 0:
        return {
            "allowed": False,
            "reason": "SCRAP_REFERENCE_CAPITAL_UNAVAILABLE",
            "sector_weight_pct": None,
            "company_weight_pct": None,
            "projected_sector_weight_pct": None,
            "projected_company_weight_pct": None,
        }

    rows = list(positions or [])
    existing_total = sum(_position_notional(row) for row in rows)
    same_company = sum(
        _position_notional(row)
        for row in rows
        if str(row.get("symbol") or "").strip().upper() == symbol
    )
    same_sector = sum(
        _position_notional(row)
        for row in rows
        if str(row.get("sector") or "").strip().upper() == sector
    )

    # The isolated paper reference capital is the denominator, not broker cash
    # and not the user's real Dhan portfolio.
    company_weight = same_company / capital * 100.0
    sector_weight = same_sector / capital * 100.0
    projected_company = (same_company + candidate) / capital * 100.0
    projected_sector = (same_sector + candidate) / capital * 100.0

    if projected_sector > SCRAP_SECTOR_LIMIT_PCT:
        return {
            "allowed": False,
            "reason": "SCRAP_REJECTION_SECTOR_EXPOSURE",
            "sector_weight_pct": round(sector_weight, 4),
            "company_weight_pct": round(company_weight, 4),
            "projected_sector_weight_pct": round(projected_sector, 4),
            "projected_company_weight_pct": round(projected_company, 4),
            "sector_limit_pct": SCRAP_SECTOR_LIMIT_PCT,
            "company_limit_pct": SCRAP_COMPANY_LIMIT_PCT,
            "existing_total_notional": round(existing_total, 4),
            "candidate_notional": round(candidate, 4),
        }

    if projected_company > SCRAP_COMPANY_LIMIT_PCT:
        return {
            "allowed": False,
            "reason": "SCRAP_REJECTION_COMPANY_EXPOSURE",
            "sector_weight_pct": round(sector_weight, 4),
            "company_weight_pct": round(company_weight, 4),
            "projected_sector_weight_pct": round(projected_sector, 4),
            "projected_company_weight_pct": round(projected_company, 4),
            "sector_limit_pct": SCRAP_SECTOR_LIMIT_PCT,
            "company_limit_pct": SCRAP_COMPANY_LIMIT_PCT,
            "existing_total_notional": round(existing_total, 4),
            "candidate_notional": round(candidate, 4),
        }

    return {
        "allowed": True,
        "reason": None,
        "sector_weight_pct": round(sector_weight, 4),
        "company_weight_pct": round(company_weight, 4),
        "projected_sector_weight_pct": round(projected_sector, 4),
        "projected_company_weight_pct": round(projected_company, 4),
        "sector_limit_pct": SCRAP_SECTOR_LIMIT_PCT,
        "company_limit_pct": SCRAP_COMPANY_LIMIT_PCT,
        "existing_total_notional": round(existing_total, 4),
        "candidate_notional": round(candidate, 4),
    }
