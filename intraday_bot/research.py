from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from .event_news_risk import event_news_gate, fetch_event_news
from .nse_fo import market_context, oi_context
from .nse_preopen import market_context as preopen_market_context, stock_context as preopen_stock_context

SCRAP_SECTOR_LIMIT_PCT = 15.0
SCRAP_COMPANY_LIMIT_PCT = 25.0
FRAMEWORKS = ("Buffett", "Rakesh Jhunjhunwala", "Peter Lynch", "100 Baggers", "CANSLIM")
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
    if value <= points[0][0]: return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if value <= x1:
            span = x1 - x0
            return y0 if span == 0 else y0 + (value - x0) * (y1 - y0) / span
    return points[-1][1]


def canslim_inputs(stock_daily: Any, benchmark_daily: Any = None, market_regime: dict[str, Any] | None = None) -> dict[str, Any]:
    """Derive CANSLIM inputs only from genuine historical market data.

    Relative strength is the stock's trailing 20-session return minus NIFTY's
    trailing 20-session return. When a benchmark frame is not supplied, the
    NIFTY return is taken from the same verified market-regime calculation used
    by the runtime. Market trend is that verified NIFTY regime score.
    """
    import pandas as pd

    def clean(frame: Any) -> pd.DataFrame:
        if frame is None or getattr(frame, "empty", True): return pd.DataFrame()
        x = frame.copy()
        if "timestamp" not in x.columns or "close" not in x.columns: return pd.DataFrame()
        x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True, errors="coerce")
        x["close"] = pd.to_numeric(x["close"], errors="coerce")
        return x.dropna(subset=["timestamp", "close"]).sort_values("timestamp")

    stock = clean(stock_daily)
    if len(stock) < 21:
        return {"status": "DATA UNAVAILABLE", "relative_strength": None, "market_trend": None, "relative_strength_basis": "20_SESSION_RETURN_DIFFERENTIAL_VS_NIFTY_50", "market_trend_basis": "VERIFIED_NIFTY_REGIME_SCORE"}
    stock_ret = float((stock["close"].iloc[-1] / stock["close"].iloc[-21] - 1.0) * 100)
    benchmark = clean(benchmark_daily)
    benchmark_ret = None
    if len(benchmark) >= 21:
        benchmark_ret = float((benchmark["close"].iloc[-1] / benchmark["close"].iloc[-21] - 1.0) * 100)
    if benchmark_ret is None:
        candidate = (market_regime or {}).get("indices", {}).get("NIFTY_50", {}).get("return_20_pct")
        if isinstance(candidate, (int, float)) and not isinstance(candidate, bool): benchmark_ret = float(candidate)
    nifty = (market_regime or {}).get("indices", {}).get("NIFTY_50", {})
    market_trend = nifty.get("score")
    if not isinstance(market_trend, (int, float)) or isinstance(market_trend, bool) or benchmark_ret is None:
        return {"status": "DATA UNAVAILABLE", "relative_strength": None, "stock_return_20_pct": round(stock_ret, 4), "benchmark_return_20_pct": benchmark_ret, "market_trend": None, "relative_strength_basis": "20_SESSION_RETURN_DIFFERENTIAL_VS_NIFTY_50", "market_trend_basis": "VERIFIED_NIFTY_REGIME_SCORE"}
    relative_strength = round(stock_ret - benchmark_ret, 4)
    return {"status": "AVAILABLE", "relative_strength": relative_strength, "stock_return_20_pct": round(stock_ret, 4), "benchmark_return_20_pct": round(benchmark_ret, 4), "market_trend": float(market_trend), "relative_strength_basis": "20_SESSION_RETURN_DIFFERENTIAL_VS_NIFTY_50", "market_trend_basis": "VERIFIED_NIFTY_REGIME_SCORE"}


def scrap_portfolio_exposure_check(symbol: str, sector: str, candidate_notional: float, reference_capital: float, positions: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Evaluate SCRAP's portfolio limits using actual simulated positions only."""
    if reference_capital <= 0: return {"allowed": False, "reason": "SCRAP_REJECTION:INVALID_REFERENCE_CAPITAL"}
    symbol = str(symbol or "").upper()
    sector = str(sector or "UNKNOWN").upper()
    rows = [p for p in (positions or []) if str(p.get("mode", "PAPER")).upper() in {"PAPER", "LIVE_TEST"}]
    company_existing = sum(float(p.get("entry_price") or p.get("current_price") or 0) * int(p.get("quantity") or 0) for p in rows if str(p.get("symbol", "")).upper() == symbol)
    sector_existing = sum(float(p.get("entry_price") or p.get("current_price") or 0) * int(p.get("quantity") or 0) for p in rows if str(p.get("sector") or "UNKNOWN").upper() == sector)
    projected_company = (company_existing + max(0.0, float(candidate_notional))) / reference_capital * 100.0
    projected_sector = (sector_existing + max(0.0, float(candidate_notional))) / reference_capital * 100.0
    if projected_sector > SCRAP_SECTOR_LIMIT_PCT:
        return {"allowed": False, "reason": "SCRAP_REJECTION:SECTOR_EXPOSURE", "sector_weight_pct": projected_sector, "company_weight_pct": projected_company, "sector_limit_pct": SCRAP_SECTOR_LIMIT_PCT, "company_limit_pct": SCRAP_COMPANY_LIMIT_PCT}
    if projected_company > SCRAP_COMPANY_LIMIT_PCT:
        return {"allowed": False, "reason": "SCRAP_REJECTION:COMPANY_EXPOSURE", "sector_weight_pct": projected_sector, "company_weight_pct": projected_company, "sector_limit_pct": SCRAP_SECTOR_LIMIT_PCT, "company_limit_pct": SCRAP_COMPANY_LIMIT_PCT}
    return {"allowed": True, "reason": "SCRAP_PORTFOLIO_LIMITS_PASS", "sector_weight_pct": projected_sector, "company_weight_pct": projected_company, "sector_limit_pct": SCRAP_SECTOR_LIMIT_PCT, "company_limit_pct": SCRAP_COMPANY_LIMIT_PCT}


def scrap_analysis(symbol: str, fundamentals: dict[str, Any] | None) -> ResearchResult:
    f = fundamentals or {}
    sectors_pct = _numeric(f, "sector_weight_pct")
    company_pct = _numeric(f, "company_weight_pct")
    red_flags = [str(x) for x in (f.get("red_flags") or [])]
    if red_flags: return ResearchResult(symbol, status="REJECTED", rejection_reason="RED_FLAG", metrics={"red_flags": red_flags})
    if sectors_pct is not None and sectors_pct > SCRAP_SECTOR_LIMIT_PCT: return ResearchResult(symbol, status="REJECTED", rejection_reason="SCRAP_REJECTION", metrics={"sector_weight_pct": sectors_pct, "limit_pct": SCRAP_SECTOR_LIMIT_PCT})
    if company_pct is not None and company_pct > SCRAP_COMPANY_LIMIT_PCT: return ResearchResult(symbol, status="REJECTED", rejection_reason="SCRAP_REJECTION", metrics={"company_weight_pct": company_pct, "limit_pct": SCRAP_COMPANY_LIMIT_PCT})
    event_risk = f.get("event_news_risk")
    if isinstance(event_risk, dict):
        level = str(event_risk.get("risk_level") or "UNKNOWN").upper()
        if level == "HIGH": return ResearchResult(symbol, status="REJECTED", rejection_reason="EVENT_NEWS_HIGH_RISK", metrics={"event_news_risk": event_risk})
        if level == "ELEVATED": return ResearchResult(symbol, status="REJECTED", rejection_reason="EVENT_NEWS_REVIEW_REQUIRED", metrics={"event_news_risk": event_risk})
        if level == "UNKNOWN": return ResearchResult(symbol, status="REJECTED", rejection_reason="EVENT_NEWS_DATA_UNAVAILABLE", metrics={"event_news_risk": event_risk})
    checks = ("profit_growth", "roce", "predictability", "earnings_quality")
    present = [v for v in (_numeric(f, k) for k in checks) if v is not None]
    score = min(10.0, 5.0 + sum(1.0 for v in present if v > 0)) if present else 0.0
    return ResearchResult(symbol, scrap_score=score, status="PASS" if present else "DATA UNAVAILABLE", metrics={"available_checks": len(present), "total_checks": len(checks), "event_news_gate": event_risk or {"risk_level": "NOT_EVALUATED"}})


def _fundamental_component_scores(f: dict[str, Any]) -> tuple[dict[str, float], dict[str, str]]:
    values: dict[str, float] = {}
    notes: dict[str, str] = {}
    specs = {"profit_growth": ("Profit Growth %", [(-20.0, 0.0), (0.0, 4.0), (10.0, 6.0), (20.0, 8.0), (30.0, 10.0)], "Higher sustainable profit growth scores better."), "eps_growth": ("EPS Growth %", [(-20.0, 0.0), (0.0, 4.0), (10.0, 6.0), (20.0, 8.0), (30.0, 10.0)], "Higher sustainable EPS growth scores better."), "roce": ("ROCE %", [(0.0, 0.0), (5.0, 4.0), (10.0, 6.0), (15.0, 8.0), (20.0, 10.0)], "Higher returns on capital score better."), "roe": ("ROE %", [(0.0, 0.0), (5.0, 4.0), (10.0, 6.0), (15.0, 8.0), (20.0, 10.0)], "Higher returns on equity score better."), "earnings_quality": ("Earnings Quality", [(0.0, 0.0), (0.6, 5.0), (1.0, 7.0), (2.0, 9.0), (3.0, 10.0)], "Operating-cash-flow to net-income quality ratio; stronger is better."), "debt_to_equity": ("Debt / Equity", [(0.5, 10.0), (1.0, 8.0), (1.5, 7.0), (2.5, 5.0), (4.0, 3.0), (6.0, 1.0)], "Lower leverage scores better for non-financial companies.")}
    for key, (_, points, note) in specs.items():
        value = _numeric(f, key)
        if value is None: continue
        values[key] = _clamp(_band(value, points)); notes[key] = note
    return values, notes


def fundamental_score(f: dict[str, Any] | None) -> float:
    d = f or {}; scores, _ = _fundamental_component_scores(d); sector_name = str(d.get("sector") or "").strip().lower(); financial = any(x in sector_name for x in ("financial", "bank", "insurance", "capital markets", "credit"))
    weights = {"profit_growth": 0.30, "eps_growth": 0.15, "roe": 0.25, "earnings_quality": 0.20, "roce": 0.05, "debt_to_equity": 0.05} if financial else {"profit_growth": 0.25, "eps_growth": 0.10, "roce": 0.15, "roe": 0.15, "earnings_quality": 0.15, "debt_to_equity": 0.20}
    available = [(k, weights[k]) for k in weights if k in scores]
    if not available: return 0.0
    total_weight = sum(w for _, w in available)
    return round(sum(scores[k] * w for k, w in available) / total_weight, 2)


def _valuation_component(value: float, kind: str) -> float:
    tables = {"pe": [(8.0, 10.0), (12.0, 9.5), (15.0, 8.5), (20.0, 7.5), (25.0, 6.5), (30.0, 5.5), (40.0, 4.0), (60.0, 2.0)], "forward_pe": [(8.0, 10.0), (12.0, 9.5), (15.0, 8.5), (20.0, 7.5), (25.0, 6.5), (30.0, 5.5), (40.0, 4.0), (60.0, 2.0)], "price_to_sales": [(1.0, 9.5), (2.0, 8.5), (3.0, 7.5), (5.0, 6.0), (8.0, 4.5), (12.0, 3.0), (20.0, 1.5)], "enterprise_to_revenue": [(1.0, 9.5), (2.0, 8.5), (3.0, 7.5), (5.0, 6.0), (8.0, 4.5), (12.0, 3.0), (20.0, 1.5)], "enterprise_to_ebitda": [(6.0, 9.5), (8.0, 9.0), (12.0, 8.0), (16.0, 7.0), (20.0, 6.0), (30.0, 4.5), (40.0, 3.0), (60.0, 1.5)], "peg_ratio": [(0.5, 10.0), (1.0, 9.0), (1.5, 8.0), (2.0, 6.5), (3.0, 4.5), (4.0, 3.0), (6.0, 1.5)], "price_to_book": [(0.75, 9.5), (1.0, 9.0), (1.5, 8.0), (2.0, 7.0), (3.0, 5.5), (5.0, 3.5), (8.0, 2.0), (12.0, 1.0)]}
    return round(_clamp(_band(value, tables[kind])), 2)


def valuation_analysis(f: dict[str, Any] | None) -> dict[str, Any]:
    d = f or {}; sector_name = str(d.get("sector") or "").strip().lower(); financial = any(x in sector_name for x in ("financial", "bank", "insurance", "capital markets", "credit")); components: list[dict[str, Any]] = []
    pe = _numeric(d, "pe")
    if pe is not None and pe > 0: components.append({"metric": "P/E", "value": pe, "score": _valuation_component(pe, "pe"), "basis": "TRAILING_EARNINGS"})
    else:
        forward_pe = _numeric(d, "forward_pe")
        if forward_pe is not None and forward_pe > 0: components.append({"metric": "Forward P/E", "value": forward_pe, "score": _valuation_component(forward_pe, "forward_pe"), "basis": "FORWARD_EARNINGS"})
    ps = _numeric(d, "price_to_sales"); ev_revenue = _numeric(d, "enterprise_to_revenue")
    if ps is not None and ps > 0: components.append({"metric": "P/S", "value": ps, "score": _valuation_component(ps, "price_to_sales"), "basis": "TRAILING_REVENUE"})
    elif ev_revenue is not None and ev_revenue > 0: components.append({"metric": "EV/Revenue", "value": ev_revenue, "score": _valuation_component(ev_revenue, "enterprise_to_revenue"), "basis": "TRAILING_REVENUE"})
    ev_ebitda = _numeric(d, "enterprise_to_ebitda")
    if ev_ebitda is not None and ev_ebitda > 0: components.append({"metric": "EV/EBITDA", "value": ev_ebitda, "score": _valuation_component(ev_ebitda, "enterprise_to_ebitda"), "basis": "POSITIVE_EBITDA"})
    peg = _numeric(d, "peg_ratio")
    if peg is not None and peg > 0: components.append({"metric": "PEG", "value": peg, "score": _valuation_component(peg, "peg_ratio"), "basis": "GROWTH_ADJUSTED"})
    pb = _numeric(d, "price_to_book")
    if financial and pb is not None and pb > 0: components.append({"metric": "P/B", "value": pb, "score": _valuation_component(pb, "price_to_book"), "basis": "FINANCIAL_SECTOR"})
    if not components:
        reason = "Trailing P/E is not meaningful because earnings are non-positive and no valid alternative valuation multiple was supplied." if pe is not None and pe <= 0 else "No valid source-backed valuation multiple was supplied."
        return {"score": 0.0, "status": "DATA UNAVAILABLE", "method": "NONE", "components": [], "reason": reason}
    weights = {"P/E": 0.30, "Forward P/E": 0.25, "P/S": 0.25, "EV/Revenue": 0.25, "EV/EBITDA": 0.20, "PEG": 0.15, "P/B": 0.20}; weighted = sum(item["score"] * weights.get(item["metric"], 0.15) for item in components); total_weight = sum(weights.get(item["metric"], 0.15) for item in components); score = round(weighted / total_weight, 2) if total_weight else 0.0; methods = ", ".join(item["metric"] for item in components)
    return {"score": score, "status": "AVAILABLE", "method": methods, "components": components, "reason": "Score uses only valid source-backed valuation multiples."}


def valuation_score(f: dict[str, Any] | None) -> float: return float(valuation_analysis(f)["score"])


def source_valuation(eps: float | None, pe: float | None) -> float | None:
    return float(eps) * float(pe) if isinstance(eps, (int, float)) and isinstance(pe, (int, float)) else None


def source_roce(profit: float | None, capital: float | None) -> float | None:
    return float(profit) / float(capital) * 100 if isinstance(profit, (int, float)) and isinstance(capital, (int, float)) and capital else None


def _factor_result(f: dict[str, Any], key: str) -> tuple[str, str]:
    value = _numeric(f, key)
    if value is None: return "MISSING", "No source value supplied"
    if key == "pe":
        if value <= 0: return "NEGATIVE", f"P/E={value:g} is not a valid positive valuation multiple"
        if value < 20: return "POSITIVE", f"P/E={value:g} is in the preferred valuation range"
        if value > 30: return "NEGATIVE", f"P/E={value:g} is above the preferred valuation range"
        return "NEUTRAL", f"P/E={value:g} is in the intermediate valuation range"
    if key == "debt_to_equity":
        if value < 1.5: return "POSITIVE", f"Debt/Equity={value:g} is below 1.5"
        if value >= 3.0: return "NEGATIVE", f"Debt/Equity={value:g} is 3.0 or higher"
        return "NEUTRAL", f"Debt/Equity={value:g} is between 1.5 and 3.0"
    if key == "earnings_quality":
        if value <= 0: return "NEGATIVE", f"Earnings quality={value:g} is non-positive"
        if value >= 0.6: return "POSITIVE", f"Earnings quality={value:g} meets 0.6 threshold"
        return "NEUTRAL", f"Earnings quality={value:g} is positive but below 0.6"
    if key == "predictability":
        if value >= 0.7: return "POSITIVE", f"Predictability={value:g} is at least 0.7"
        if value < 0.5: return "NEGATIVE", f"Predictability={value:g} is below 0.5"
        return "NEUTRAL", f"Predictability={value:g} is between 0.5 and 0.7"
    if key == "roce":
        if value >= 12: return "POSITIVE", f"ROCE={value:g} meets 12% research threshold"
        if value < 4: return "NEGATIVE", f"ROCE={value:g} is below 4%"
        return "NEUTRAL", f"ROCE={value:g} is between 4% and 12%"
    if key == "roe":
        if value >= 12: return "POSITIVE", f"ROE={value:g} meets 12% research threshold"
        if value < 5: return "NEGATIVE", f"ROE={value:g} is below 5%"
        return "NEUTRAL", f"ROE={value:g} is between 5% and 12%"
    if key in {"profit_growth", "eps_growth"}:
        if value >= 10: return "POSITIVE", f"{key}={value:g} is at least 10%"
        if value < 0: return "NEGATIVE", f"{key}={value:g} is negative"
        return "NEUTRAL", f"{key}={value:g} is positive but below 10%"
    if key == "relative_strength":
        if value > 0: return "POSITIVE", f"20-session return differential vs NIFTY={value:g}%"
        if value < 0: return "NEGATIVE", f"20-session return differential vs NIFTY={value:g}%"
        return "NEUTRAL", "20-session return differential vs NIFTY is 0%"
    if key == "market_trend":
        if value >= 6.5: return "POSITIVE", f"Verified NIFTY market-trend score={value:g}/10"
        if value <= 3.5: return "NEGATIVE", f"Verified NIFTY market-trend score={value:g}/10"
        return "NEUTRAL", f"Verified NIFTY market-trend score={value:g}/10"
    return ("POSITIVE", f"{key}={value:g} is positive") if value > 0 else ("NEGATIVE", f"{key}={value:g} is not positive")


def framework_analysis(f: dict[str, Any] | None) -> dict[str, Any]:
    d = f or {}; output: dict[str, Any] = {}
    for name, spec in FRAMEWORK_RULES.items():
        positives: list[str] = []; neutrals: list[str] = []; negatives: list[str] = []; missing: list[str] = []; evidence: list[str] = []
        for key in spec["factors"]:
            state, text = _factor_result(d, key); evidence.append(text)
            if state == "POSITIVE": positives.append(key)
            elif state == "NEUTRAL": neutrals.append(key)
            elif state == "NEGATIVE": negatives.append(key)
            else: missing.append(key)
        available = len(positives) + len(neutrals) + len(negatives); score = round(_clamp(5.0 + (len(positives) - len(negatives)) * 1.0), 2) if available else 0.0; confidence = round(available / len(spec["factors"]), 2)
        output[name] = {"score": score, "confidence": confidence, "positive_factors": positives, "neutral_factors": neutrals, "negative_factors": negatives, "missing_data": missing, "evidence": evidence, "method": spec["label"]}
    scores = [x["score"] for x in output.values() if x["confidence"] > 0]; overall = round(sum(scores) / len(scores), 2) if scores else 0.0; spread = max(scores, default=0.0) - min(scores, default=0.0); agreement = "AGREE" if scores and spread <= 2 else ("DISAGREE" if scores else "DATA UNAVAILABLE")
    return {"frameworks": output, "overall": overall, "agreement": agreement, "status": "AVAILABLE" if scores else "DATA UNAVAILABLE"}


def conviction(f: dict[str, Any] | None) -> dict[str, Any]: return framework_analysis(f)


def research_bundle(symbol: str, f: dict[str, Any] | None) -> dict[str, Any]:
    d = f if isinstance(f, dict) else {}; derivatives = oi_context(symbol); market = market_context(); preopen = preopen_stock_context(symbol); preopen_market = preopen_market_context(); news = fetch_event_news(symbol); news_gate = event_news_gate(news); d["event_news_risk"] = news_gate; scrap = scrap_analysis(symbol, d); frameworks = framework_analysis(d); valuation = valuation_analysis(d)
    return {"symbol": symbol, "scrap": asdict(scrap), "fundamental_score": fundamental_score(d), "valuation_score": valuation["score"], "valuation": valuation, "frameworks": frameworks, "derivatives": derivatives, "market_context": market, "preopen": preopen, "preopen_market_context": preopen_market, "event_news": news, "event_news_risk": news_gate, "valuation_price_from_eps_pe": source_valuation(d.get("eps"), d.get("pe")), "roce_from_profit_capital": source_roce(d.get("profit"), d.get("capital")), "status": "REJECTED" if scrap.rejection_reason else ("AVAILABLE" if frameworks["status"] == "AVAILABLE" else "DATA UNAVAILABLE")}


def research_dict(result: ResearchResult) -> dict[str, Any]: return asdict(result)
