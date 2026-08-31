from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


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


def scrap_analysis(symbol: str, fundamentals: dict[str, Any] | None) -> ResearchResult:
    """Source-preserving SCRAP research layer; red flags reject, missing data is not negative."""
    f = fundamentals or {}
    sectors_pct = f.get("sector_weight_pct")
    company_pct = f.get("company_weight_pct")
    red_flags = list(f.get("red_flags") or [])
    # Source-derived thresholds: SECTORS >15%, COMPANIES >25%, RED FLAGS -> REJECTION.
    if red_flags:
        return ResearchResult(symbol, status="REJECTED", rejection_reason="RED_FLAG", metrics={"red_flags": red_flags})
    if sectors_pct is not None and float(sectors_pct) > 15:
        return ResearchResult(symbol, status="REJECTED", rejection_reason="SCRAP_REJECTION", metrics={"sector_weight_pct": sectors_pct})
    if company_pct is not None and float(company_pct) > 25:
        return ResearchResult(symbol, status="REJECTED", rejection_reason="SCRAP_REJECTION", metrics={"company_weight_pct": company_pct})
    vals = [f.get(k) for k in ("profit_growth", "roce", "predictability", "earnings_quality")]
    available = [float(v) for v in vals if isinstance(v, (int, float))]
    score = min(10.0, max(0.0, 5.0 + sum(1 for v in available if v > 0) * 1.0)) if available else 0.0
    return ResearchResult(symbol, scrap_score=score, status="PASS" if available else "DATA UNAVAILABLE", metrics=f)


def fundamental_score(f: dict[str, Any] | None) -> float:
    if not f: return 0.0
    score = 5.0
    for key, good, bad in (("profit_growth", 0, -10), ("roce", 12, -5), ("roe", 12, -5), ("earnings_quality", 0.6, -2), ("debt_to_equity", 1.5, -2)):
        v = f.get(key)
        if not isinstance(v, (int, float)): continue
        if key == "debt_to_equity": score += 1 if v < good else bad
        elif v >= good: score += 1
        else: score += bad
    return max(0.0, min(10.0, score))


def valuation_score(f: dict[str, Any] | None) -> float:
    pe = (f or {}).get("pe")
    if not isinstance(pe, (int, float)) or pe <= 0: return 0.0
    if pe < 15: return 8.0
    if pe < 25: return 6.0
    if pe < 40: return 4.0
    return 2.0


def conviction(f: dict[str, Any] | None) -> dict[str, Any]:
    """Research-only framework score; never a standalone intraday trigger."""
    d = f or {}
    frameworks = {}
    for name in ("Buffett", "Rakesh Jhunjhunwala", "Peter Lynch", "100 Baggers", "CANSLIM"):
        score = 5.0
        positives, negatives, missing = [], [], []
        for key in ("profit_growth", "roce", "predictability", "eps_growth", "relative_strength"):
            value = d.get(key)
            if value is None: missing.append(key)
            elif isinstance(value, (int, float)) and value > 0: score += 1; positives.append(key)
            else: score -= 1; negatives.append(key)
        frameworks[name] = {"score": max(0, min(10, score)), "positive_factors": positives, "negative_factors": negatives, "missing_data": missing, "confidence": 1-len(missing)/5}
    scores = [v["score"] for v in frameworks.values()]
    overall = sum(scores)/len(scores) if scores else 0
    return {"frameworks": frameworks, "overall": overall, "agreement": "AGREE" if max(scores, default=0)-min(scores, default=0) <= 2 else "DISAGREE"}


def source_valuation(eps: float | None, pe: float | None) -> float | None:
    if isinstance(eps, (int, float)) and isinstance(pe, (int, float)):
        return float(eps) * float(pe)  # STOCK PRICE = EPS × P/E
    return None


def source_roce(profit: float | None, capital: float | None) -> float | None:
    if isinstance(profit, (int, float)) and isinstance(capital, (int, float)) and capital:
        return float(profit) / float(capital) * 100  # ROCE = PROFIT / CAPITAL × 100
    return None


def research_dict(result: ResearchResult) -> dict[str, Any]:
    return asdict(result)
