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
    return float(value) if isinstance(value, (int, float)) else None


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


def fundamental_score(f: dict[str, Any] | None) -> float:
    if not f:
        return 0.0
    score = 5.0
    for key, good, bad in (("profit_growth", 0, -10), ("roce", 12, -5), ("roe", 12, -5), ("earnings_quality", 0.6, -2), ("debt_to_equity", 1.5, -2)):
        v = _numeric(f, key)
        if v is None:
            continue
        if key == "debt_to_equity":
            score += 1 if v < good else bad
        elif v >= good:
            score += 1
        else:
            score += bad
    return max(0.0, min(10.0, score))


def valuation_score(f: dict[str, Any] | None) -> float:
    pe = _numeric(f or {}, "pe")
    if pe is None or pe <= 0:
        return 0.0
    if pe < 15: return 8.0
    if pe < 25: return 6.0
    if pe < 40: return 4.0
    return 2.0


def source_valuation(eps: float | None, pe: float | None) -> float | None:
    if isinstance(eps, (int, float)) and isinstance(pe, (int, float)):
        return float(eps) * float(pe)
    return None


def source_roce(profit: float | None, capital: float | None) -> float | None:
    if isinstance(profit, (int, float)) and isinstance(capital, (int, float)) and capital:
        return float(profit) / float(capital) * 100
    return None


def _factor_result(f: dict[str, Any], key: str) -> tuple[str, str]:
    value = f.get(key)
    if value is None:
        return "MISSING", "No source value supplied"
    if not isinstance(value, (int, float)):
        return "MISSING", "Source value is not numeric"
    if key == "pe":
        return ("POSITIVE", f"P/E={value:g} is within the research valuation band") if 0 < value < 25 else ("NEGATIVE", f"P/E={value:g} is outside the preferred band")
    if key == "debt_to_equity":
        return ("POSITIVE", f"Debt/Equity={value:g} is below 1.5") if value < 1.5 else ("NEGATIVE", f"Debt/Equity={value:g} is 1.5 or higher")
    if key == "earnings_quality":
        return ("POSITIVE", f"Earnings quality={value:g} meets 0.6 threshold") if value >= 0.6 else ("NEGATIVE", f"Earnings quality={value:g} is below 0.6")
    return ("POSITIVE", f"{key}={value:g} is positive") if value > 0 else ("NEGATIVE", f"{key}={value:g} is not positive")


def framework_analysis(f: dict[str, Any] | None) -> dict[str, Any]:
    """Return auditable per-framework evidence, including missing data and confidence."""
    d = f or {}
    output: dict[str, Any] = {}
    for name, spec in FRAMEWORK_RULES.items():
        positives: list[str] = []
        negatives: list[str] = []
        missing: list[str] = []
        evidence: list[str] = []
        for key in spec["factors"]:
            state, text = _factor_result(d, key)
            evidence.append(text)
            if state == "POSITIVE": positives.append(key)
            elif state == "NEGATIVE": negatives.append(key)
            else: missing.append(key)
        available = len(positives) + len(negatives)
        score = round((5.0 + (len(positives) - len(negatives)) * 0.9) if available else 0.0, 2)
        score = max(0.0, min(10.0, score))
        confidence = round(available / len(spec["factors"]), 2)
        output[name] = {"score": score, "confidence": confidence, "positive_factors": positives, "negative_factors": negatives, "missing_data": missing, "evidence": evidence, "method": spec["label"]}
    scores = [x["score"] for x in output.values() if x["confidence"] > 0]
    overall = round(sum(scores) / len(scores), 2) if scores else 0.0
    spread = max(scores, default=0) - min(scores, default=0)
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
