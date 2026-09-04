from __future__ import annotations

# Active production research/paper-trading runtime.
# Deterministic data and risk gates own every trading decision; AI is advisory only.

import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time as clock, timezone
from pathlib import Path
from typing import Any

from .ai_advisor import advisory
from .alerts import telegram
from .brokers import DhanBroker
from .config import IST, settings
from .database import Database
from .fundamentals_cache import get as get_fundamentals, refresh_batch as refresh_fundamentals_batch
from .market_regime import build as build_market_regime
from .paper import fill
from .research import research_bundle, fundamental_score, scrap_analysis
from .risk import position_size, risk_gate
from .sector_intelligence import build as build_sector_intelligence, membership as sector_membership
from .technical import technical_setup

LIVE_TEST_MODE = "LIVE_TEST"
PAPER_MODE = "PAPER"


def market_open() -> bool:
    n = datetime.now(IST)
    return n.weekday() < 5 and clock(9, 15) <= n.time() <= clock(15, 30)


def universe() -> list[dict[str, Any]]:
    p = Path("data/universe.json")
    if not p.exists():
        return []
    try:
        x = json.loads(p.read_text(encoding="utf-8"))
        return x if isinstance(x, list) else []
    except Exception:
        return []


def fundamentals(symbol: str, current_price: float | None = None) -> dict[str, Any]:
    return get_fundamentals(symbol, current_price=current_price)


def quote(q: dict[str, Any]) -> tuple[float, float, float]:
    o = q.get("ohlc") or q.get("OHLC") or {}

    def n(v: Any) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    return (
        n(q.get("last_price", q.get("ltp", q.get("lastPrice")))),
        n(q.get("prev_close", q.get("previousClose", o.get("close")))),
        n(q.get("volume", q.get("volumeTraded", q.get("volumeTradedToday")))),
    )


def sector(s: str) -> str:
    groups = {
        "BANKING": {"HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK"},
        "IT": {"TCS", "INFY", "WIPRO", "HCLTECH", "TECHM"},
        "ENERGY": {"RELIANCE", "ONGC", "NTPC", "POWERGRID", "COALINDIA"},
        "AUTO": {"MARUTI", "M&M", "EICHERMOT", "TATAMOTORS"},
        "PHARMA": {"SUNPHARMA", "CIPLA", "DRREDDY"},
        "METALS": {"TATASTEEL", "HINDALCO", "JSWSTEEL"},
        "FMCG": {"ITC", "HINDUNILVR"},
    }
    return next((k for k, v in groups.items() if s in v), "OTHER")


def _source_sector(item: dict[str, Any], f: dict[str, Any], membership_cache: dict[str, Any] | None = None) -> str:
    symbol = str(item.get("symbol", "")).upper()
    if membership_cache:
        source_sector = (membership_cache.get("symbol_sector") or {}).get(symbol)
        if isinstance(source_sector, str) and source_sector.strip():
            return source_sector.strip().upper()
    for value in (item.get("sector"), item.get("industry"), f.get("sector")):
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return sector(symbol)


def _verified_funds(broker: DhanBroker) -> tuple[float | None, str]:
    try:
        raw = broker.funds()
        value = float(raw)
        if value >= 0:
            return value, "DHAN"
    except Exception:
        pass
    return None, "UNAVAILABLE"


def _research_gate(bundle: dict[str, Any], f: dict[str, Any]) -> tuple[bool, str | None]:
    status = str(bundle.get("status") or "DATA UNAVAILABLE").upper()
    if status == "REJECTED":
        return False, str(bundle.get("scrap", {}).get("rejection_reason") or "RESEARCH_REJECTION")
    if status != "AVAILABLE":
        return False, "RESEARCH_DATA_UNAVAILABLE"
    frameworks = bundle.get("frameworks") or {}
    if str(frameworks.get("status")) != "AVAILABLE":
        return False, "FRAMEWORK_DATA_UNAVAILABLE"
    core = ("profit_growth", "eps_growth", "roce", "roe", "earnings_quality", "predictability")
    if not any(isinstance(f.get(k), (int, float)) and not isinstance(f.get(k), bool) for k in core):
        return False, "FUNDAMENTAL_DATA_UNAVAILABLE"
    return True, None


def analyse(broker, item, price: float, volume: float, funds: float | None, market_regime: dict[str, Any], membership_cache: dict[str, Any] | None = None) -> dict[str, Any]:
    symbol = str(item["symbol"]).upper()
    f = fundamentals(symbol, current_price=price)
    bundle = research_bundle(symbol, f)
    scrap = scrap_analysis(symbol, f)
    sector_name = _source_sector(item, f, membership_cache)
    base: dict[str, Any] = {
        "symbol": symbol,
        "sector": sector_name,
        "sector_source": "NSE_OFFICIAL_SECTOR_INDICES" if membership_cache and symbol in (membership_cache.get("symbol_sector") or {}) else "SECONDARY_METADATA_OR_LEGACY_FALLBACK",
        "theme": "UNKNOWN",
        "price": price,
        "decision": "NO TRADE",
        "scrap_score": scrap.scrap_score,
        "fundamental_score": fundamental_score(f),
        "valuation_score": bundle["valuation_score"],
        "conviction_score": bundle["frameworks"]["overall"],
        "framework_status": bundle["status"],
        "framework_agreement": bundle["frameworks"]["agreement"],
        "frameworks": bundle["frameworks"]["frameworks"],
        "derivatives": bundle.get("derivatives", {}),
        "market_context": bundle.get("market_context", {}),
        "preopen_market_context": bundle.get("preopen_market_context", {}),
        "market_regime": market_regime,
        "liquidity_score": 5.0,
        "volume_score": min(10.0, volume / 1e6),
        "market_score": 5.0,
        "news_score": 5.0,
        "ai_score": 0.0,
        "ai_consensus": "NOT_RUN",
    }
    if scrap.rejection_reason:
        base.update(reason="SCRAP rejection", rejection_reason=scrap.rejection_reason, research=bundle)
        return base
    research_ok, research_why = _research_gate(bundle, f)
    if not research_ok:
        base.update(reason="Research/data gate failed", rejection_reason=research_why, research=bundle)
        return base
    if funds is None:
        base.update(reason="Verified broker funds unavailable", rejection_reason="FUNDS_DATA_UNAVAILABLE", research=bundle)
        return base
    try:
        h = broker.history(str(item["security_id"]), item.get("exchange_segment", "NSE_EQ"), 5, instrument="EQUITY")
    except Exception as exc:
        base.update(reason="DATA UNAVAILABLE", rejection_reason="DATA_ERROR", data_error=str(exc), research=bundle)
        return base
    if len(h) < 60:
        base.update(reason="DATA UNAVAILABLE", rejection_reason="DATA_ERROR", research=bundle)
        return base
    try:
        daily = broker.daily_history(str(item["security_id"]), item.get("exchange_segment", "NSE_EQ"), instrument="EQUITY")
    except Exception as exc:
        base.update(reason="MTF RSI data unavailable", rejection_reason="MTF_RSI_DATA_UNAVAILABLE", data_error=str(exc), research=bundle)
        return base
    if len(daily) < 300:
        base.update(reason="MTF RSI history insufficient", rejection_reason="MTF_RSI_DATA_UNAVAILABLE", research=bundle)
        return base
    t = technical_setup(h, daily_history=daily)
    base.update({
        "entry": t["entry"], "entry_zone": [t["entry_low"], t["entry_high"]],
        "entry_low": t["entry_low"], "entry_high": t["entry_high"],
        "max_chase": t["max_chase"], "stop": t["stop"], "target": t["target"],
        "rr": t["rr"], "trend_score": t["trend_score"],
        "technical_score": t["technical_score"], "trend_state": t["trend_state"],
        "transition": t.get("transition"), "technical_indicators": t.get("indicators", {}),
        "mtf_rsi": t.get("mtf_rsi", {}), "mtf_rsi_agreement": t.get("mtf_rsi_agreement", False),
        "mtf_rsi_reason": t.get("mtf_rsi_reason"), "research": bundle,
    })
    if t["direction"] == "HOLD":
        base.update(reason="No valid intraday direction", rejection_reason="TECHNICAL_REJECTION")
        return base
    combined = str(market_regime.get("combined_regime", "UNAVAILABLE"))
    if t["direction"] == "BUY" and not bool(market_regime.get("buy_allowed")):
        base.update(reason=f"Market regime blocks BUY: {combined}", rejection_reason="MARKET_REGIME_GATE")
        return base
    if t["direction"] == "SELL" and not bool(market_regime.get("sell_allowed")):
        base.update(reason=f"Market regime blocks SELL: {combined}", rejection_reason="MARKET_REGIME_GATE")
        return base
    if not t.get("mtf_rsi_agreement"):
        base.update(reason=f"MTF RSI gate failed: {t.get('mtf_rsi_reason')}", rejection_reason=t.get("mtf_rsi_reason", "MTF_RSI_GATE"))
        return base
    ok, why = risk_gate(t["rr"], 0, 0, 0)
    if not ok:
        base.update(reason="Risk gate failed", rejection_reason=why)
        return base
    size = position_size(
        t["entry"], t["stop"], t["target"], settings.reference_capital, funds,
        liquidity_qty=max(1, int(volume / 1000) if volume else 1),
    )
    if not size.quantity:
        base.update(reason="No safe affordable quantity", rejection_reason="INSUFFICIENT_FUNDS")
        return base
    base.update({
        "decision": t["direction"], "quantity": size.quantity,
        "capital_required": size.capital_required, "max_risk": size.max_risk,
        "potential_reward": size.potential_reward,
        "overall_score": t["trend_score"] * .30 + base["fundamental_score"] * .10
        + base["conviction_score"] * .10 + base["volume_score"] * .10
        + (10.0 if combined == "BULLISH" and t["direction"] == "BUY" else 10.0 if combined == "BEARISH" and t["direction"] == "SELL" else 0.0) * .20
        + 5 * .10,
        "reason": f"Market={combined}; Sector={sector_name}; Trend={t['trend_state']}; MTF_RSI={t['mtf_rsi_reason']}; R:R={t['rr']:.2f}; Frameworks={bundle['frameworks']['agreement']}; OI={bundle.get('derivatives', {}).get('signal', 'UNAVAILABLE')}",
    })
    return base


def _charges(turnover: float) -> float:
    brokerage = min(40.0, turnover * .0003)
    stt = turnover * .00025
    exchange = turnover * .0000307
    sebi = turnover / 10_000_000 * 10
    stamp = turnover * .00003
    gst = (brokerage + exchange + sebi) * .18
    return brokerage + stt + exchange + sebi + stamp + gst


def _manage_positions(db: Database, qmap: dict[str, dict[str, Any]], uni: list[dict[str, Any]]) -> None:
    by_symbol = {x["symbol"]: x for x in uni}
    now_utc = datetime.now(timezone.utc).isoformat()
    now_ist = datetime.now(IST)
    with db.connect() as con:
        rows = con.execute("SELECT * FROM positions WHERE closed_at IS NULL").fetchall()
        for p in rows:
            item = by_symbol.get(p["symbol"])
            if not item:
                continue
            price, _, _ = quote(qmap.get(str(item.get("security_id")), {}))
            if price <= 0:
                continue
            side = p["side"]
            reason = None
            if side == "BUY" and price <= p["stop"]: reason = "STOP_LOSS"
            elif side == "BUY" and price >= p["target"]: reason = "TARGET"
            elif side == "SELL" and price >= p["stop"]: reason = "STOP_LOSS"
            elif side == "SELL" and price <= p["target"]: reason = "TARGET"
            if now_ist.time() >= clock(settings.square_off_hour, settings.square_off_minute): reason = "EOD_SQUARE_OFF"
            con.execute("UPDATE positions SET current_price=? WHERE position_id=?", (price, p["position_id"]))
            if not reason: continue
            gross = (price - p["entry_price"]) * p["quantity"] * (1 if side == "BUY" else -1)
            charges = _charges((price + p["entry_price"]) * p["quantity"])
            net = gross - charges
            trade_id = "TRD-" + uuid.uuid4().hex[:16]
            con.execute("UPDATE positions SET current_price=?,closed_at=?,pnl=? WHERE position_id=?", (price, now_utc, net, p["position_id"]))
            con.execute("INSERT INTO trades(trade_id,signal_id,symbol,mode,side,quantity,entry_price,exit_price,gross_pnl,charges,net_pnl,exit_reason,opened_at,closed_at,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (trade_id, None, p["symbol"], p["mode"], side, p["quantity"], p["entry_price"], price, gross, charges, net, reason, p["opened_at"], now_utc, p["payload"]))
            db.event("execution", "INFO", "POSITION_CLOSED", {"trade_id": trade_id, "net_pnl": net, "exit_reason": reason}, p["symbol"], p["mode"])


def _record_rejection(db: Database, cycle_id: str, candidate: dict[str, Any], mode: str) -> None:
    payload = dict(candidate)
    payload["cycle_id"] = cycle_id
    payload["record_type"] = "REJECTED_SIGNAL"
    db.event("strategy", "INFO", "SIGNAL_REJECTED", payload, candidate.get("symbol"), mode)


def _dynamic_analysis_pool(ranked: list[tuple[float, dict[str, Any], float, float]]) -> list[tuple[float, dict[str, Any], float, float]]:
    if not ranked: return []
    ranked_count = min(len(ranked), max(20, int(getattr(settings, "deep_analysis_count", 80))))
    top_count = max(1, int(ranked_count * 0.75))
    pool = ranked[:top_count]
    remainder = ranked[top_count:]
    if remainder:
        cycle_number = int(time.time() // 300)
        rotating_count = min(ranked_count - len(pool), len(remainder))
        if rotating_count > 0:
            start = (cycle_number * rotating_count) % len(remainder)
            rotated = remainder[start:start + rotating_count]
            if len(rotated) < rotating_count: rotated += remainder[:rotating_count - len(rotated)]
            pool.extend(rotated)
    return pool


def _portfolio_snapshot(db: Database, capital: float, mode: str) -> dict[str, Any]:
    with db.connect() as con:
        rows = con.execute("SELECT symbol, quantity, entry_price FROM positions WHERE closed_at IS NULL AND mode=?", (mode,)).fetchall()
        today = datetime.now(IST).date().isoformat()
        pnl_row = con.execute("SELECT COALESCE(SUM(net_pnl),0) FROM trades WHERE mode=? AND closed_at IS NOT NULL AND substr(closed_at,1,10)=?", (mode, today)).fetchone()
    exposure = sum(float(r["entry_price"] or 0) * int(r["quantity"] or 0) for r in rows)
    daily_pnl = float(pnl_row[0] or 0) if pnl_row else 0.0
    return {"open_positions": len(rows), "open_exposure": exposure, "daily_loss": max(0.0, -daily_pnl), "daily_pnl": daily_pnl, "deployment_limit": capital * settings.max_capital_deployment}


def _sector_exposure(db: Database, capital: float, mode: str, target_sector: str, extra_notional: float = 0.0, membership_cache: dict[str, Any] | None = None) -> float:
    if capital <= 0: return 1.0
    if membership_cache is None:
        try:
            membership_cache = sector_membership()
        except Exception:
            membership_cache = {}
    by_symbol = membership_cache.get("symbol_sector") or {}
    with db.connect() as con:
        rows = con.execute("SELECT symbol, quantity, entry_price FROM positions WHERE closed_at IS NULL AND mode=?", (mode,)).fetchall()
    total = sum(float(r["entry_price"] or 0) * int(r["quantity"] or 0) for r in rows if str(by_symbol.get(str(r["symbol"]).upper(), "OTHER")).upper() == str(target_sector).upper())
    return (total + extra_notional) / capital


def _run_ai_advisory(candidates: list[dict[str, Any]]) -> None:
    if not (os.getenv("OPENAI_API_KEY", "").strip() or os.getenv("ANTHROPIC_API_KEY", "").strip()): return
    try: limit = max(0, min(3, int(os.getenv("AI_ADVISORY_MAX_CANDIDATES", "3"))))
    except ValueError: limit = 3
    for candidate in candidates[:limit]:
        context = {k: candidate.get(k) for k in ("symbol", "decision", "sector", "price", "entry", "stop", "target", "rr", "trend_score", "technical_score", "fundamental_score", "valuation_score", "framework_agreement", "derivatives", "market_context", "market_regime", "mtf_rsi")}
        result = advisory("Advisory only. Never override deterministic market-regime, sector, MTF-RSI, risk, funds, data, execution or reconciliation gates. Return concise JSON with score 0-10, decision BUY/SELL/HOLD/NO TRADE, confidence 0-1, positives, negatives, risks.\n" + json.dumps(context, default=str))
        candidate["ai_advisory"] = result
        candidate["ai_consensus"] = "ADVISORY_AVAILABLE" if result.get("status") == "AVAILABLE" else ("ADVISORY_ERROR" if result.get("status") == "ERROR" else "NOT_CONFIGURED")
        if result.get("text"): candidate["ai_advisory_text"] = str(result["text"])[:4000]


def _persist_morning_snapshot(recommendations: list[dict[str, Any]]) -> None:
    """Persist the first validated morning set without overwriting a user import."""
    now = datetime.now(IST)
    if now.weekday() >= 5 or not (clock(9, 15) <= now.time() <= clock(11, 0)) or not recommendations:
        return
    path = Path("data/morning_recommendations") / f"{now.date().isoformat()}.json"
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    records = []
    for item in recommendations:
        record = dict(item)
        record["source"] = "BOT_AUTO_MORNING_SNAPSHOT"
        record["purchase_status"] = "NOT_A_BROKER_FILL"
        record["recommendation_id"] = f"{now.date().isoformat()}:{str(item.get('symbol','')).upper()}:{str(item.get('decision','')).upper()}"
        records.append(record)
    payload = {
        "date": now.date().isoformat(),
        "source": "BOT_AUTO_MORNING_SNAPSHOT",
        "generated_at": now.isoformat(),
        "purchase_status": "NOT_A_BROKER_FILL",
        "records": records,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _telegram_cycle_message(result: dict[str, Any], funds: float | None, sector_info: dict[str, Any]) -> str:
    lines = [
        "📊 STOCKMARKET BOT — PAPER CYCLE",
        f"Time: {datetime.now(IST).strftime('%d-%b-%Y %H:%M:%S')} IST",
        f"Universe: {result['stocks_observed']} | Quotes: {result['quotes']} | Deep analysis: {result['deep_analysis_pool']}",
        f"Market regime: {result.get('market_regime', {}).get('combined_regime', 'UNAVAILABLE')}",
        f"Recommendations: {len(result.get('candidates', []))} | Paper orders: {len(result['orders'])} | Execution rejected: {result.get('execution_rejected_candidates', 0)}",
        f"Dhan verified available funds: {'UNAVAILABLE' if funds is None else f'₹{funds:,.2f}'}",
        f"Paper reference capital: ₹{settings.reference_capital:,.2f}",
        f"Suggested BUY investment: ₹{result['suggested_buy_investment']:,.2f}",
        f"Suggested SELL value: ₹{result['suggested_sell_value']:,.2f}",
        f"Open positions: {result.get('positions_open', 0)}",
    ]
    sectors = sector_info.get("sectors") or {}
    ranked_sectors = sorted(sectors.items(), key=lambda kv: float(kv[1].get("strength", 0) or 0), reverse=True)
    if ranked_sectors:
        top = ranked_sectors[:3]
        weak = sorted(sectors.items(), key=lambda kv: float(kv[1].get("strength", 0) or 0))[:2]
        lines.append("Sector leaders: " + ", ".join(f"{name} {row.get('strength', 0):.1f}/10 ({row.get('breadth_pct', 0):.0f}% breadth)" for name, row in top))
        lines.append("Sector weak: " + ", ".join(f"{name} {row.get('strength', 0):.1f}/10" for name, row in weak))
    if result["candidates"]:
        lines.append("🔎 VALIDATED TRADE RECOMMENDATIONS")
        for c in result["candidates"][:5]:
            execution = c.get("execution_status", "NOT_ATTEMPTED")
            execution_reason = c.get("execution_rejection_reason")
            suffix = f" | Execution {execution}" + (f" ({execution_reason})" if execution_reason else "")
            lines.append(
                f"{c['decision']} {c['symbol']} | Sector {c.get('sector','OTHER')} | Entry ₹{float(c.get('entry',0)):.2f} | SL ₹{float(c.get('stop',0)):.2f} | Target ₹{float(c.get('target',0)):.2f} | Qty {int(c.get('quantity',0))} | R:R {float(c.get('rr',0)):.2f} | Score {float(c.get('overall_score',0)):.1f}{suffix}"
            )
            lines.append(f"Reason: {str(c.get('reason',''))[:260]}")
            if c.get("ai_advisory_text"):
                lines.append(f"AI advisory: {str(c['ai_advisory_text'])[:260]}")
    else:
        lines.append("🔎 VALIDATED TRADE RECOMMENDATIONS: NONE — no candidate passed every deterministic research/market/technical gate.")
        if result["rejections"]:
            top = sorted(result["rejections"].items(), key=lambda x: x[1], reverse=True)[:8]
            lines.append("Top rejection reasons: " + ", ".join(f"{k}={v}" for k, v in top))
        details = result.get("rejection_details") or []
        for c in sorted(details, key=lambda x: float(x.get("trend_score", 0) or 0), reverse=True)[:3]:
            lines.append(f"Near-miss: {c.get('symbol','?')} | {c.get('rejection_reason','NO_TRADE')} | {str(c.get('reason',''))[:180]}")
    if result.get("errors"):
        lines.append("Cycle errors: " + " | ".join(str(x) for x in result["errors"][:3]))
    return "\n".join(lines)[:3900]


def run_cycle(mode: str = PAPER_MODE) -> dict[str, Any]:
    mode = str(mode or PAPER_MODE).upper()
    if mode not in {PAPER_MODE, LIVE_TEST_MODE}: raise ValueError("mode must be PAPER or LIVE_TEST")
    start = time.monotonic()
    db = Database()
    cycle_id = uuid.uuid4().hex
    result: dict[str, Any] = {"cycle_id": cycle_id, "started_at": datetime.now(timezone.utc).isoformat(), "mode": mode, "market_open": market_open(), "stocks_observed": 0, "quotes": 0, "deep_analysis_pool": 0, "dynamic_pool": True, "fundamentals_cache_hits": 0, "fundamentals_refreshed": 0, "fundamentals_refresh_errors": 0, "candidates": [], "recommendations": [], "rejections": {}, "rejection_details": [], "orders": [], "errors": [], "suggested_buy_investment": 0.0, "suggested_sell_value": 0.0, "execution_rejected_candidates": 0, "execution_accepted_candidates": 0}
    db.event("engine", "INFO", "CYCLE_START", result)
    uni = universe()
    result["stocks_observed"] = len(uni)
    if not uni:
        result["errors"].append("DATA_UNAVAILABLE: empty universe")
        return finish(result, start, db)
    broker = DhanBroker()
    try:
        qmap = broker.bulk_quotes(uni)
        result["quotes"] = len(qmap)
    except Exception as exc:
        result["errors"].append("DATA_ERROR: " + str(exc))
        db.event("market_data", "ERROR", "DATA_ERROR", {"error": str(exc)}, mode=mode)
        return finish(result, start, db)

    try:
        market_regime = build_market_regime(broker)
        result["market_regime"] = market_regime
        db.event("market", "INFO", "MARKET_REGIME", market_regime, mode=mode)
    except Exception as exc:
        result["market_regime"] = {"status": "UNAVAILABLE", "combined_regime": "UNAVAILABLE", "buy_allowed": False, "sell_allowed": False, "error": str(exc)}
        result["errors"].append("MARKET_REGIME_UNAVAILABLE: " + str(exc))
        return finish(result, start, db)

    try:
        sector_info = build_sector_intelligence(uni, qmap)
        membership_cache = sector_membership()
        result["sector_intelligence"] = {
            "status": sector_info.get("status", "UNAVAILABLE"),
            "source": sector_info.get("source"),
            "classified_symbols": sector_info.get("classified_symbols", 0),
            "unclassified_universe_symbols": sector_info.get("unclassified_universe_symbols", 0),
            "as_of": sector_info.get("as_of"),
            "cache_warning": sector_info.get("cache_warning"),
        }
        db.event("market", "INFO", "SECTOR_INTELLIGENCE", result["sector_intelligence"], mode=mode)
    except Exception as exc:
        sector_info = {"status": "UNAVAILABLE", "sectors": {}, "error": str(exc)}
        membership_cache = {}
        result["sector_intelligence"] = sector_info
        result["errors"].append("SECTOR_INTELLIGENCE_UNAVAILABLE: " + str(exc))

    ranked = []
    for item in uni:
        p, prev, v = quote(qmap.get(str(item["security_id"]), {}))
        if p > 0:
            score = abs(p / prev - 1) * 100 + min(v / 1e6, 5) if prev else min(v / 1e6, 5)
            ranked.append((score, item, p, v))
    ranked.sort(reverse=True, key=lambda x: x[0])
    analysis_pool = _dynamic_analysis_pool(ranked)
    result["deep_analysis_pool"] = len(analysis_pool)
    refresh_inputs = [(item["symbol"], price) for _, item, price, _ in analysis_pool]
    before_cache = {}
    try:
        raw_cache_path = Path("data/fundamentals.json")
        if raw_cache_path.exists():
            payload = json.loads(raw_cache_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict): before_cache = {str(k).upper(): v for k, v in payload.items() if isinstance(v, dict)}
    except Exception:
        before_cache = {}
    try:
        cache = refresh_fundamentals_batch(refresh_inputs)
        result["fundamentals_refreshed"] = sum(1 for symbol, _ in refresh_inputs if symbol.upper() in cache and cache.get(symbol.upper(), {}).get("fetched_at") and cache.get(symbol.upper(), {}).get("fetched_at") != before_cache.get(symbol.upper(), {}).get("fetched_at"))
        result["fundamentals_cache_hits"] = sum(1 for _, item, _, _ in analysis_pool if item["symbol"].upper() in cache and item["symbol"].upper() in before_cache)
    except Exception as exc:
        result["fundamentals_refresh_errors"] += 1
        result["fundamentals_refresh_error"] = str(exc)
    funds, funds_source = _verified_funds(broker)
    result["verified_funds"] = funds
    result["funds_source"] = funds_source
    if funds is None:
        result["rejections"]["FUNDS_DATA_UNAVAILABLE"] = len(analysis_pool)
        result["errors"].append("FUNDS_DATA_UNAVAILABLE: broker funds could not be verified")
    else:
        with ThreadPoolExecutor(max_workers=settings.scan_workers) as ex:
            futures = [ex.submit(analyse, broker, item, p, v, funds, market_regime, membership_cache) for _, item, p, v in analysis_pool]
            for fut in as_completed(futures):
                try:
                    c = fut.result()
                    db.event("research", "INFO", "FRAMEWORK_ANALYSIS", c.get("research", {}), c.get("symbol"), mode)
                    if c["decision"] in {"BUY", "SELL"}:
                        result["candidates"].append(c)
                    else:
                        reason = c.get("rejection_reason") or "NO_TRADE"
                        result["rejections"][reason] = result["rejections"].get(reason, 0) + 1
                        result["rejection_details"].append(c)
                        _record_rejection(db, cycle_id, c, mode)
                except Exception as exc:
                    result["errors"].append("ANALYSIS_ERROR: " + str(exc))
    result["candidates"].sort(key=lambda x: x.get("overall_score", 0), reverse=True)
    _run_ai_advisory(result["candidates"])
    _manage_positions(db, qmap, uni)
    if result["market_open"]:
        capital = float(settings.reference_capital)
        with db.connect() as con:
            rows = con.execute("SELECT symbol FROM positions WHERE closed_at IS NULL AND mode=?", (mode,)).fetchall()
            open_symbols = {r["symbol"] for r in rows}
        for c in result["candidates"]:
            why = None
            if c["symbol"] in open_symbols:
                why = "DUPLICATE_ORDER"
                c["execution_status"] = "REJECTED"
                c["execution_rejection_reason"] = why
                c["reason"] = "Position already open"
            elif c["decision"] == "BUY" and c["price"] > c["max_chase"]:
                why = "ENTRY_EXPIRED"
                c["execution_status"] = "REJECTED"
                c["execution_rejection_reason"] = why
                c["reason"] = "BUY entry exceeded max-chase price"
            elif c["decision"] == "SELL" and c["price"] < c["max_chase"]:
                why = "ENTRY_EXPIRED"
                c["execution_status"] = "REJECTED"
                c["execution_rejection_reason"] = why
                c["reason"] = "SELL entry exceeded max-chase distance"
            else:
                state = _portfolio_snapshot(db, capital, mode)
                notional = float(c.get("capital_required", 0) or 0)
                if state["open_positions"] >= settings.max_positions:
                    why = "POSITION_LIMIT"
                elif state["daily_loss"] >= settings.daily_loss_limit:
                    why = "DAILY_LOSS_LIMIT"
                elif state["open_exposure"] + notional > state["deployment_limit"]:
                    why = "CAPITAL_DEPLOYMENT_LIMIT"
                else:
                    sector_fraction = _sector_exposure(db, capital, mode, c.get("sector", "OTHER"), notional, membership_cache)
                    ok, risk_why = risk_gate(float(c.get("rr", 0) or 0), state["daily_loss"], state["open_positions"], sector_fraction)
                    why = None if ok else risk_why
                if why:
                    c["execution_status"] = "REJECTED"
                    c["execution_rejection_reason"] = why
                    c["reason"] = f"Execution risk gate: {why}"
            if why:
                result["rejections"][why] = result["rejections"].get(why, 0) + 1
                result["rejection_details"].append(dict(c))
                result["execution_rejected_candidates"] += 1
                _record_rejection(db, cycle_id, c, mode)
                continue
            sid = "SIG-" + uuid.uuid4().hex[:16]
            c["signal_id"] = sid
            c["execution_status"] = "EXECUTED_PAPER"
            db.signal(sid, c["symbol"], c["decision"], c)
            oid = fill(db, c, mode=mode)
            open_symbols.add(c["symbol"])
            result["orders"].append({"order_id": oid, "signal_id": sid, "status": "FILLED", "mode": mode, "symbol": c["symbol"], "side": c["decision"], "quantity": c["quantity"], "price": c["entry"]})
            result["execution_accepted_candidates"] += 1
            if c["decision"] == "BUY": result["suggested_buy_investment"] += notional
            else: result["suggested_sell_value"] += float(c.get("entry", 0) or 0) * int(c.get("quantity", 0) or 0)
    else:
        for c in result["candidates"]:
            c["execution_status"] = "NOT_ATTEMPTED_MARKET_CLOSED"
    result["recommendations"] = [dict(c) for c in result["candidates"]]
    result["validated_candidate_count"] = len(result["candidates"])
    result["suggested_buy_investment"] = round(result["suggested_buy_investment"], 2)
    result["suggested_sell_value"] = round(result["suggested_sell_value"], 2)
    result["execution_gate"] = "LIVE_TEST_SIMULATION" if mode == LIVE_TEST_MODE else "PAPER_MODE"
    _persist_morning_snapshot(result["recommendations"])
    if settings.telegram_token and settings.telegram_chat_id:
        telegram(_telegram_cycle_message(result, funds, sector_info))
    return finish(result, start, db)


def finish(result: dict[str, Any], start: float, db: Database) -> dict[str, Any]:
    result["duration_seconds"] = round(time.monotonic() - start, 3)
    result["ended_at"] = datetime.now(IST).isoformat()
    result["positions_open"] = int(db.scalar("SELECT COUNT(*) FROM positions WHERE closed_at IS NULL") or 0)
    result["realized_pnl"] = float(db.scalar("SELECT COALESCE(SUM(net_pnl),0) FROM trades") or 0)
    result["today_realized_pnl"] = float(db.scalar("SELECT COALESCE(SUM(net_pnl),0) FROM trades WHERE mode IN ('PAPER','LIVE_TEST') AND substr(closed_at,1,10)=?", (datetime.now(IST).date().isoformat(),)) or 0)
    with db.connect() as con:
        con.execute("INSERT OR REPLACE INTO cycles(cycle_id,started_at,ended_at,status,payload) VALUES(?,?,?,?,?)", (result["cycle_id"], result["started_at"], result["ended_at"], "ERROR" if result.get("errors") else "COMPLETED", json.dumps(result, default=str)))
    Path("data").mkdir(exist_ok=True)
    Path("data/monitor_status.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    db.event("engine", "ERROR" if result.get("errors") else "INFO", "CYCLE_END", result, mode=result.get("mode", PAPER_MODE))
    return result
