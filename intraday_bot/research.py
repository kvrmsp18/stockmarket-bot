from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from .nse_fo import market_context, oi_context
from .nse_preopen import market_context as preopen_market_context, stock_context as preopen_stock_context


SCRAP_SECTOR_LIMIT_PCT = 15.0
SCRAP_COMPANY_LIMIT_PCT = 25.0
FRAMEWORKS = ("Buffett", "Rakesh Jhunjhunwala", "Peter Lynch", "100 Baggers", "CANSLIM")

# These are explicit engineering mappings for the named research frameworks.
# They are research/conviction inputs only; the source rules prohibit using them
# as standalone intraday triggers.
FRAMEWORK_RULES: dict[str, dict[str, Any]] = {
    "Buffett": {"label": "Quality / economics / predictability / valuation", "factors": ("profit_growth", "roce", "predictability", "earnings_quality", "debt_to_equity", "pe")},
    "Rakesh Jhunjhunwala": {"label": "Growth / earnings / operating leverage / long runway", "factors": ("profit_growth", "eps_growth", "roce", "predictability", "earnings_quality")},
    "Peter Lynch": {"label": "Growth relative to price / earnings / business simplicity", "factors": ("profit_growth", "eps_growth", "pe", "predictability", "roe")},
    "100 Baggers": {"label": "High returns / reinvestment / long compounding runway", "factors": ("profit_growth", "eps_growth", "roce", "roe", "debt_to_equity", "predictability")},
    "CANSLIM": {"label": "Current earnings / annual growth / relative strength / market direction", "factors": ("eps_growth", "profit_growth", "relative_strength", "roce", "market_trend")},
}


@dataclass
class ResearchResult:
    symbol: str
    sector: str = "UNKNOWN"
    theme: str = "UNKNOWN"
    scrap_score: float = 0.0
    fundamental_score: float = 0.0
    valuation_score: float = 0.0
    conviction_score: float = 0.0
    status: str = "DATA UNAVAILABLE"
    rejection_reason: str | None = None
    metrics: dict[str, Any] | None = None


def _numeric(f: dict[str, Any], key: str) -> float | None:
    value = f.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _clamp(value: float, low: float = 0.0, high: float = 10.0) -> float:
    return max(low, min(high, float(value)))


def _band(value: float, points: list[tuple[float, float]]) -> float:
    """Piecewise-linear score interpolation across ascending value/score points."""
    if value <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if value <= x1:
            span = x1 - x0
            return y0 if span == 0 else y0 + (value - x0) * (y1 - y0) / span
    return points[-1][1]


def scrap_analysis(symbol: str, fundamentals: dict[str, Any] | None) -> ResearchResult:
    """SCRAP gate. Missing data is DATA UNAVAILABLE, never silently negative."""
    f = fundamentals or {}
    sectors_pct = _numeric(f, "sector_weight_pct")
    company_pct = _numeric(f, "company_weight_pct")
    red_flags = [str(x) for x in (f.get("red_flags") or [])]
    if red_flags:
        return ResearchResult(symbol, status="REJECTED", rejection_reason="RED_FLAG", metrics={"red_flags": red_flags})
    if sectors_pct is not None and sectors_pct > SCRAP_SECTOR_LIMIT_PCT:
        return ResearchResult(symbol, status="REJECTED", rejection_reason="SCRAP_REJECTION", metrics={"sector_weight_pct": sectors_pct, "limit_pct": SCRAP_SECTOR_LIMIT_PCT})
    if company_pct is not None and company_pct > SCRAP_COMPANY_LIMIT_PCT:
        return ResearchResult(symbol, status="REJECTED", rejection_reason="SCRAP_REJECTION", metrics={"company_weight_pct": company_pct, "limit_pct": SCRAP_COMPANY_LIMIT_PCT})
    checks = ("profit_growth", "roce", "predictability", "earnings_quality")
    present = [_numeric(f, k) for k in checks]
    present = [v for v in present if v is not None]
    score = min(10.0, 5.0 + sum(1.0 for v in present if v > 0)) if present else 0.0
    return ResearchResult(symbol, scrap_score=score, status="PASS" if present else "DATA UNAVAILABLE", metrics={"available_checks": len(present), "total_checks": len(checks)})


def _fundamental_component_scores(f: dict[str, Any]) -> tuple[dict[str, float], dict[str, str]]:
    values: dict[str, float] = {}
    notes: dict[str, str] = {}
    specs: dict[str, tuple[str, list[tuple[float, float]], str]] = {
        "profit_growth": ("Profit Growth %", [(-20.0, 0.0), (0.0, 4.0), (10.0, 6.0), (20.0, 8.0), (30.0, 10.0)], "Higher sustainable profit growth scores better."),
        "eps_growth": ("EPS Growth %", [(-20.0, 0.0), (0.0, 4.0), (10.0, 6.0), (20.0, 8.0), (30.0, 10.0)], "Higher sustainable EPS growth scores better."),
        "roce": ("ROCE %", [(0.0, 0.0), (5.0, 4.0), (10.0, 6.0), (15.0, 8.0), (20.0, 10.0)], "Higher returns on capital score better."),
        "roe": ("ROE %", [(0.0, 0.0), (5.0, 4.0), (10.0, 6.0), (15.0, 8.0), (20.0, 10.0)], "Higher returns on equity score better."),
        "earnings_quality": ("Earnings Quality", [(0.0, 0.0), (0.6, 5.0), (1.0, 7.0), (2.0, 9.0), (3.0, 10.0)], "Operating-cash-flow to net-income quality ratio; stronger is better."),
        "debt_to_equity": ("Debt / Equity", [(0.5, 10.0), (1.0, 8.0), (1.5, 7.0), (2.5, 5.0), (4.0, 3.0), (6.0, 1.0)], "Lower leverage scores better."),
    }
    for key, (_, points, note) in specs.items():
        value = _numeric(f, key)
        if value is None:
            continue
        values[key] = _clamp(_band(value, points))
        notes[key] = note
    return values, notes


def fundamental_score(f: dict[str, Any] | None) -> float:
    """Continuous, weighted 0-10 fundamental score over available source fields."""
    d = f or {}
    scores, _ = _fundamental_component_scores(d)
    weights = {
        "profit_growth": 0.25,
        "eps_growth": 0.10,
        "roce": 0.15,
        "roe": 0.15,
        "earnings_quality": 0.15,
        "debt_to_equity": 0.20,
    }
    available = [(k, weights[k]) for k in weights if k in scores]
    if not available:
        return 0.0
    total_weight = sum(w for _, w in available)
    return round(sum(scores[k] * w for k, w in available) / total_weight, 2)


def valuation_score(f: dict[str, Any] | None) -> float:
    pe = _numeric(f or {}, "pe")
    if pe is None or pe <= 0:
        return 0.0
    return round(_clamp(_band(pe, [(10.0, 9.0), (15.0, 8.0), (20.0, 7.0), (25.0, 6.0), (30.0, 5.0), (40.0, 4.0), (60.0, 2.0)])), 2)


def source_valuation(eps: float | None, pe: float | None) -> float | None:
    if isinstance(eps, (int, float)) and isinstance(pe, (int, float)):
        return float(eps) * float(pe)
    return None


def source_roce(profit: float | None, capital: float | None) -> float | None:
    if isinstance(profit, (int, float)) and isinstance(capital, (int, float)) and capital:
        return float(profit) / float(capital) * 100
    return None


def _factor_result(f: dict[str, Any], key: str) -> tuple[str, str]:
    value = _numeric(f, key)
    if value is None:
        return "MISSING", "No source value supplied"
    if key == "pe":
        if value <= 0:
            return "NEGATIVE", f"P/E={value:g} is not a valid positive valuation multiple"
        if value < 20:
            return "POSITIVE", f"P/E={value:g} is in the preferred valuation range"
        if value > 30:
            return "NEGATIVE", f"P/E={value:g} is above the preferred valuation range"
        return "NEUTRAL", f"P/E={value:g} is in the intermediate valuation range"
    if key == "debt_to_equity":
        if value < 1.5:
            return "POSITIVE", f"Debt/Equity={value:g} is below 1.5"
        if value >= 3.0:
            return "NEGATIVE", f"Debt/Equity={value:g} is 3.0 or higher"
        return "NEUTRAL", f"Debt/Equity={value:g} is between 1.5 and 3.0"
    if key == "earnings_quality":
        if value <= 0:
            return "NEGATIVE", f"Earnings quality={value:g} is non-positive"
        if value >= 0.6:
            return "POSITIVE", f"Earnings quality={value:g} meets 0.6 threshold"
        return "NEUTRAL", f"Earnings quality={value:g} is positive but below 0.6"
    if key == "predictability":
        if value >= 0.7:
            return "POSITIVE", f"Predictability={value:g} is at least 0.7"
        if value < 0.5:
            return "NEGATIVE", f"Predictability={value:g} is below 0.5"
        return "NEUTRAL", f"Predictability={value:g} is between 0.5 and 0.7"
    if key == "roce":
        if value >= 12:
            return "POSITIVE", f"ROCE={value:g} meets 12% research threshold"
        if value < 4:
            return "NEGATIVE", f"ROCE={value:g} is below 4%"
        return "NEUTRAL", f"ROCE={value:g} is between 4% and 12%"
    if key == "roe":
        if value >= 12:
            return "POSITIVE", f"ROE={value:g} meets 12% research threshold"
        if value < 5:
            return "NEGATIVE", f"ROE={value:g} is below 5%"
        return "NEUTRAL", f"ROE={value:g} is between 5% and 12%"
    if key in {"profit_growth", "eps_growth"}:
        if value >= 10:
            return "POSITIVE", f"{key}={value:g} is at least 10%"
        if value < 0:
            return "NEGATIVE", f"{key}={value:g} is negative"
        return "NEUTRAL", f"{key}={value:g} is positive but below 10%"
    return ("POSITIVE", f"{key}={value:g} is positive") if value > 0 else ("NEGATIVE", f"{key}={value:g} is not positive")


def framework_analysis(f: dict[str, Any] | None) -> dict[str, Any]:
    """Auditable five-framework research with positive/neutral/negative/missing states."""
    d = f or {}
    output: dict[str, Any] = {}
    for name, spec in FRAMEWORK_RULES.items():
        positives: list[str] = []
        neutrals: list[str] = []
        negatives: list[str] = []
        missing: list[str] = []
        evidence: list[str] = []
        for key in spec["factors"]:
            state, text = _factor_result(d, key)
            evidence.append(text)
            if state == "POSITIVE":
                positives.append(key)
            elif state == "NEUTRAL":
                neutrals.append(key)
            elif state == "NEGATIVE":
                negatives.append(key)
            else:
                missing.append(key)
        available = len(positives) + len(neutrals) + len(negatives)
        score = round(_clamp(5.0 + (len(positives) - len(negatives)) * 1.0), 2) if available else 0.0
        confidence = round(available / len(spec["factors"]), 2)
        output[name] = {
            "score": score,
            "confidence": confidence,
            "positive_factors": positives,
            "neutral_factors": neutrals,
            "negative_factors": negatives,
            "missing_data": missing,
            "evidence": evidence,
            "method": spec["label"],
        }
    scores = [x["score"] for x in output.values() if x["confidence"] > 0]
    overall = round(sum(scores) / len(scores), 2) if scores else 0.0
    spread = max(scores, default=0.0) - min(scores, default=0.0)
    agreement = "AGREE" if scores and spread <= 2 else ("DISAGREE" if scores else "DATA UNAVAILABLE")
    return {"frameworks": output, "overall": overall, "agreement": agreement, "status": "AVAILABLE" if scores else "DATA UNAVAILABLE"}


def conviction(f: dict[str, Any] | None) -> dict[str, Any]:
    """Compatibility wrapper: research conviction never acts as an intraday trigger."""
    return framework_analysis(f)


def research_bundle(symbol: str, f: dict[str, Any] | None) -> dict[str, Any]:
    d = f or {}
    scrap = scrap_analysis(symbol, d)
    frameworks = framework_analysis(d)
    derivatives = oi_context(symbol)
    market = market_context()
    preopen = preopen_stock_context(symbol)
    preopen_market = preopen_market_context()
    return {
        "symbol": symbol,
        "scrap": asdict(scrap),
        "fundamental_score": fundamental_score(d),
        "valuation_score": valuation_score(d),
        "frameworks": frameworks,
        "derivatives": derivatives,
        "market_context": market,
        "preopen": preopen,
        "preopen_market_context": preopen_market,
        "valuation_price_from_eps_pe": source_valuation(d.get("eps"), d.get("pe")),
        "roce_from_profit_capital": source_roce(d.get("profit"), d.get("capital")),
        "status": "REJECTED" if scrap.rejection_reason else ("AVAILABLE" if frameworks["status"] == "AVAILABLE" else "DATA UNAVAILABLE"),
    }


def research_dict(result: ResearchResult) -> dict[str, Any]:
    return asdict(result)
