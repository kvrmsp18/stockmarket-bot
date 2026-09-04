from __future__ import annotations

"""Non-trading portfolio and basket analytics.

This module intentionally does not place, modify, or cancel orders. It supplies
pure advisory calculations that can be rendered by the existing Portfolio UI.
No stock constituents are invented: callers must provide the basket symbols or
actual open positions.
"""

from dataclasses import dataclass, asdict
from typing import Any, Iterable


@dataclass(frozen=True)
class BasketDefinition:
    name: str
    description: str
    symbols: tuple[str, ...]
    benchmark: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RebalanceAdvice:
    symbol: str
    action: str
    current_weight_pct: float
    target_weight_pct: float
    delta_pct: float
    reason: str
    severity: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_symbols(symbols: Iterable[str]) -> tuple[str, ...]:
    """Return unique NSE-style symbols without creating constituents."""
    result: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        symbol = str(raw or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        result.append(symbol)
    return tuple(result)


def build_basket(
    name: str,
    symbols: Iterable[str],
    *,
    description: str = "User-defined stock basket",
    benchmark: str | None = None,
) -> BasketDefinition:
    """Create a basket from explicitly supplied symbols only."""
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("Basket name is required")
    clean_symbols = normalize_symbols(symbols)
    if not clean_symbols:
        raise ValueError("At least one real symbol is required")
    clean_benchmark = str(benchmark).strip().upper() if benchmark else None
    return BasketDefinition(clean_name, str(description).strip(), clean_symbols, clean_benchmark)


def basket_return(prices: dict[str, Iterable[float]]) -> dict[str, Any]:
    """Calculate equal-weight basket return from supplied price histories.

    Each symbol must have at least two positive prices. Missing/invalid symbols
    are reported rather than assigned a synthetic return.
    """
    returns: dict[str, float] = {}
    unavailable: list[str] = []
    for raw_symbol, series in prices.items():
        symbol = str(raw_symbol).strip().upper()
        values: list[float] = []
        for value in series:
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number > 0:
                values.append(number)
        if len(values) < 2 or values[0] <= 0:
            unavailable.append(symbol)
            continue
        returns[symbol] = (values[-1] / values[0] - 1.0) * 100.0
    if not returns:
        return {"status": "DATA UNAVAILABLE", "basket_return_pct": None, "symbol_returns_pct": {}, "unavailable_symbols": unavailable}
    basket = sum(returns.values()) / len(returns)
    return {
        "status": "AVAILABLE",
        "basket_return_pct": round(basket, 2),
        "symbol_returns_pct": {k: round(v, 2) for k, v in returns.items()},
        "unavailable_symbols": unavailable,
    }


def benchmark_relative_return(basket_return_pct: float | None, benchmark_return_pct: float | None) -> dict[str, Any]:
    """Compare a basket with an explicitly supplied benchmark return."""
    if basket_return_pct is None or benchmark_return_pct is None:
        return {"status": "DATA UNAVAILABLE", "relative_return_pct": None}
    return {
        "status": "AVAILABLE",
        "relative_return_pct": round(float(basket_return_pct) - float(benchmark_return_pct), 2),
    }


def portfolio_weights(positions: Iterable[dict[str, Any]]) -> dict[str, float]:
    """Return weights from actual supplied open-position market values."""
    values: dict[str, float] = {}
    for position in positions:
        symbol = str(position.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        try:
            market_value = float(position.get("market_value"))
        except (TypeError, ValueError):
            continue
        if market_value > 0:
            values[symbol] = values.get(symbol, 0.0) + market_value
    total = sum(values.values())
    if total <= 0:
        return {}
    return {symbol: value / total * 100.0 for symbol, value in values.items()}


def sector_weights(positions: Iterable[dict[str, Any]]) -> dict[str, float]:
    """Return sector weights from actual supplied positions."""
    grouped: dict[str, float] = {}
    total = 0.0
    for position in positions:
        sector = str(position.get("sector") or "UNKNOWN").strip() or "UNKNOWN"
        try:
            value = float(position.get("market_value"))
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        grouped[sector] = grouped.get(sector, 0.0) + value
        total += value
    if total <= 0:
        return {}
    return {sector: value / total * 100.0 for sector, value in grouped.items()}


def rebalance_advice(
    positions: Iterable[dict[str, Any]],
    *,
    max_company_pct: float = 25.0,
    max_sector_pct: float = 15.0,
) -> dict[str, Any]:
    """Produce deterministic concentration advice from actual positions.

    The source SCRAP thresholds are preserved as the default concentration
    limits. This is advisory only; it never changes an order or position.
    """
    rows = list(positions)
    company = portfolio_weights(rows)
    sectors = sector_weights(rows)
    advice: list[RebalanceAdvice] = []

    for symbol, weight in sorted(company.items(), key=lambda item: item[1], reverse=True):
        if weight > max_company_pct:
            advice.append(RebalanceAdvice(
                symbol=symbol,
                action="REDUCE_CONCENTRATION",
                current_weight_pct=round(weight, 2),
                target_weight_pct=round(max_company_pct, 2),
                delta_pct=round(weight - max_company_pct, 2),
                reason=f"Company exposure exceeds the {max_company_pct:g}% concentration limit.",
                severity="HIGH",
            ))

    sector_symbols: dict[str, list[str]] = {}
    for position in rows:
        sector = str(position.get("sector") or "UNKNOWN").strip() or "UNKNOWN"
        symbol = str(position.get("symbol") or "").strip().upper()
        if symbol:
            sector_symbols.setdefault(sector, []).append(symbol)
    for sector, weight in sorted(sectors.items(), key=lambda item: item[1], reverse=True):
        if weight > max_sector_pct:
            symbols = ", ".join(sorted(set(sector_symbols.get(sector, []))))
            advice.append(RebalanceAdvice(
                symbol=symbols or sector,
                action="REDUCE_SECTOR_CONCENTRATION",
                current_weight_pct=round(weight, 2),
                target_weight_pct=round(max_sector_pct, 2),
                delta_pct=round(weight - max_sector_pct, 2),
                reason=f"Sector exposure exceeds the {max_sector_pct:g}% concentration limit.",
                severity="HIGH",
            ))

    if not advice:
        status = "NO_REBALANCE_FLAG"
        reason = "No supplied company or sector exposure exceeds the configured concentration limits."
    else:
        status = "REVIEW_REQUIRED"
        reason = "Concentration exceeds deterministic limits; review diversification before adding exposure."

    return {
        "status": status,
        "reason": reason,
        "company_weights_pct": {k: round(v, 2) for k, v in company.items()},
        "sector_weights_pct": {k: round(v, 2) for k, v in sectors.items()},
        "advice": [item.to_dict() for item in advice],
        "limits": {"max_company_pct": float(max_company_pct), "max_sector_pct": float(max_sector_pct)},
        "advisory_only": True,
    }
