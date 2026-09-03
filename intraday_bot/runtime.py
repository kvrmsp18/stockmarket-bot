from __future__ import annotations

# NOTE: Complete runtime is intentionally preserved here. The market is ranked
# across the full universe, while expensive historical analysis is performed
# on a dynamic rotating pool rather than a hard-coded symbol list.

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
from .paper import fill
from .research import research_bundle, fundamental_score, scrap_analysis
from .risk import position_size, risk_gate
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
    """Return the latest persisted verified fundamentals snapshot for a symbol."""
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


def analyse(broker, item, price: float, volume: float, funds: float) -> dict[str, Any]:
    symbol = item["symbol"]
    f = fundamentals(symbol, current_price=price)
    bundle = research_bundle(symbol, f)
    scrap = scrap_analysis(symbol, f)
    base: dict[str, Any] = {
        "symbol": symbol,
        "sector": sector(symbol),
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
    try:
        h = broker.history(str(item["security_id"]), item.get("exchange_segment", "NSE_EQ"), 5)
    except Exception as exc:
        base.update(reason="DATA UNAVAILABLE", rejection_reason="DATA_ERROR", data_error=str(exc), research=bundle)
        return base
    if len(h) < 60:
        base.update(reason="DATA UNAVAILABLE", rejection_reason="DATA_ERROR", research=bundle)
        return base
    t = technical_setup(h)
    base.update(
        {
            "entry": t["entry"], "entry_zone": [t["entry_low"], t["entry_high"]],
            "entry_low": t["entry_low"], "entry_high": t["entry_high"],
            "max_chase": t["max_chase"], "stop": t["stop"], "target": t["target"],
            "rr": t["rr"], "trend_score": t["trend_score"],
            "technical_score": t["technical_score"], "trend_state": t["trend_state"],
            "research": bundle,
        }
    )
    if t["direction"] == "HOLD":
        base.update(reason="No valid intraday direction", rejection_reason="TECHNICAL_REJECTION")
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
    base.update(
        {
            "decision": t["direction"], "quantity": size.quantity,
            "capital_required": size.capital_required, "max_risk": size.max_risk,
            "potential_reward": size.potential_reward,
            "overall_score": t["trend_score"] * .35 + base["fundamental_score"] * .10
            + base["conviction_score"] * .10 + base["volume_score"] * .10 + 5 * .35,
            "reason": f"Trend={t['trend_state']}; R:R={t['rr']:.2f}; Frameworks={bundle['frameworks']['agreement']}; OI={bundle.get('derivatives', {}).get('signal', 'UNAVAILABLE')}",
        }
    )
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
            if side == "BUY" and price <= p["stop"]:
                reason = "STOP_LOSS"
            elif side == "BUY" and price >= p["target"]:
                reason = "TARGET"
            elif side == "SELL" and price >= p["stop"]:
                reason = "STOP_LOSS"
            elif side == "SELL" and price <= p["target"]:
                reason = "TARGET"
            if now_ist.time() >= clock(settings.square_off_hour, settings.square_off_minute):
                reason = "EOD_SQUARE_OFF"
            con.execute("UPDATE positions SET current_price=? WHERE position_id=?", (price, p["position_id"]))
            if not reason:
                continue
            gross = (price - p["entry_price"]) * p["quantity"] * (1 if side == "BUY" else -1)
            charges = _charges((price + p["entry_price"]) * p["quantity"])
            net = gross - charges
            trade_id = "TRD-" + uuid.uuid4().hex[:16]
            con.execute("UPDATE positions SET current_price=?,closed_at=?,pnl=? WHERE position_id=?", (price, now_utc, net, p["position_id"]))
            con.execute(
                "INSERT INTO trades(trade_id,signal_id,symbol,mode,side,quantity,entry_price,exit_price,gross_pnl,charges,net_pnl,exit_reason,opened_at,closed_at,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (trade_id, None, p["symbol"], p["mode"], side, p["quantity"], p["entry_price"], price, gross, charges, net, reason, p["opened_at"], now_utc, p["payload"]),
            )
            db.event("execution", "INFO", "POSITION_CLOSED", {"trade_id": trade_id, "net_pnl": net, "exit_reason": reason}, p["symbol"], p["mode"])


def _record_rejection(db: Database, cycle_id: str, candidate: dict[str, Any], mode: str) -> None:
    payload = dict(candidate)
    payload["cycle_id"] = cycle_id
    payload["record_type"] = "REJECTED_SIGNAL"
    db.event("strategy", "INFO", "SIGNAL_REJECTED", payload, candidate.get("symbol"), mode)


def _dynamic_analysis_pool(ranked: list[tuple[float, dict[str, Any], float, float]]) -> list[tuple[float, dict[str, Any], float, float]]:
    """Select a changing analysis pool from the FULL quoted universe.

    No symbol is permanently whitelisted. The pool combines the strongest
    current movers with a rotating slice of the remainder, allowing every
    valid market symbol to reach deep analysis over successive cycles without
    forcing thousands of slow historical API calls into one five-minute run.
    """
    if not ranked:
        return []
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
            if len(rotated) < rotating_count:
                rotated += remainder[:rotating_count - len(rotated)]
            pool.extend(rotated)
    return pool


def _portfolio_snapshot(db: Database, capital: float, mode: str) -> dict[str, Any]:
    """Return current portfolio risk state for the configured mode."""
    with db.connect() as con:
        rows = con.execute(
            "SELECT symbol, quantity, entry_price FROM positions WHERE closed_at IS NULL AND mode=?",
            (mode,),
        ).fetchall()
        today = datetime.now(IST).date().isoformat()
        pnl_row = con.execute(
            "SELECT COALESCE(SUM(net_pnl),0) FROM trades WHERE mode=? AND closed_at IS NOT NULL AND substr(closed_at,1,10)=?",
            (mode, today),
        ).fetchone()
    exposure = sum(float(r["entry_price"] or 0) * int(r["quantity"] or 0) for r in rows)
    daily_pnl = float(pnl_row[0] or 0) if pnl_row else 0.0
    return {
        "open_positions": len(rows),
        "open_exposure": exposure,
        "daily_loss": max(0.0, -daily_pnl),
        "daily_pnl": daily_pnl,
        "deployment_limit": capital * settings.max_capital_deployment,
    }


def _sector_exposure(db: Database, capital: float, mode: str, target_sector: str, extra_notional: float = 0.0) -> float:
    """Return target-sector exposure including a pending candidate as a capital fraction."""
    if capital <= 0:
        return 1.0
    by_symbol = {x["symbol"]: sector(x["symbol"]) for x in universe()}
    with db.connect() as con:
        rows = con.execute(
            "SELECT symbol, quantity, entry_price FROM positions WHERE closed_at IS NULL AND mode=?",
            (mode,),
        ).fetchall()
    total = sum(
        float(r["entry_price"] or 0) * int(r["quantity"] or 0)
        for r in rows
        if by_symbol.get(r["symbol"], "OTHER") == target_sector
    )
    return (total + extra_notional) / capital


def _run_ai_advisory(candidates: list[dict[str, Any]]) -> None:
    """Attach optional AI evidence to a tiny shortlist; AI cannot change deterministic gates."""
    if not (os.getenv("OPENAI_API_KEY", "").strip() or os.getenv("ANTHROPIC_API_KEY", "").strip()):
        return
    try:
        limit = max(0, min(3, int(os.getenv("AI_ADVISORY_MAX_CANDIDATES", "3"))))
    except ValueError:
        limit = 3
    for candidate in candidates[:limit]:
        context = {k: candidate.get(k) for k in (
            "symbol", "decision", "price", "entry", "stop", "target", "rr",
            "trend_score", "technical_score", "fundamental_score", "valuation_score",
            "framework_agreement", "derivatives"
        )}
        result = advisory(
            "Advisory only. Never override deterministic risk, funds, data, execution or reconciliation gates. "
            "Return concise JSON with score 0-10, decision BUY/SELL/HOLD/NO TRADE, confidence 0-1, positives, negatives, risks.\n"
            + json.dumps(context, default=str)
        )
        candidate["ai_advisory"] = result
        candidate["ai_consensus"] = "ADVISORY_AVAILABLE" if result.get("status") == "AVAILABLE" else (
            "ADVISORY_ERROR" if result.get("status") == "ERROR" else "NOT_CONFIGURED"
        )
        if result.get("text"):
            candidate["ai_advisory_text"] = str(result["text"])[:4000]


def run_cycle(mode: str = PAPER_MODE) -> dict[str, Any]:
    mode = str(mode or PAPER_MODE).upper()
    if mode not in {PAPER_MODE, LIVE_TEST_MODE}:
        raise ValueError("mode must be PAPER or LIVE_TEST")
    start = time.monotonic()
    db = Database()
    cycle_id = uuid.uuid4().hex
    result: dict[str, Any] = {
        "cycle_id": cycle_id, "started_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode, "market_open": market_open(), "stocks_observed": 0, "quotes": 0,
        "deep_analysis_pool": 0, "dynamic_pool": True, "fundamentals_cache_hits": 0,
        "fundamentals_refreshed": 0, "fundamentals_refresh_errors": 0,
        "candidates": [], "rejections": {}, "rejection_details": [], "orders": [], "errors": [],
        "suggested_buy_investment": 0.0, "suggested_sell_value": 0.0,
    }
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
    ranked = []
    for item in uni:
        p, prev, v = quote(qmap.get(str(item["security_id"]), {}))
        if p > 0:
            score = abs(p / prev - 1) * 100 + min(v / 1e6, 5) if prev else min(v / 1e6, 5)
            ranked.append((score, item, p, v))
    ranked.sort(reverse=True, key=lambda x: x[0])
    analysis_pool = _dynamic_analysis_pool(ranked)
    result["deep_analysis_pool"] = len(analysis_pool)

    # Fundamentals are slow-moving compared with quotes. Refresh only a small
    # bounded number of stale symbols each cycle, persist the source-backed
    # snapshots, and use the cache for all analysis-pool symbols. This prevents
    # the five-minute market loop from issuing hundreds of provider requests.
    refresh_inputs = [(item["symbol"], price) for _, item, price, _ in analysis_pool]
    before_cache = {}
    try:
        raw_cache_path = Path("data/fundamentals.json")
        if raw_cache_path.exists():
            payload = json.loads(raw_cache_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                before_cache = {str(k).upper(): v for k, v in payload.items() if isinstance(v, dict)}
    except Exception:
        before_cache = {}
    try:
        cache = refresh_fundamentals_batch(refresh_inputs)
        result["fundamentals_refreshed"] = sum(
            1 for symbol, _ in refresh_inputs
            if symbol.upper() in cache and cache.get(symbol.upper(), {}).get("fetched_at")
            and cache.get(symbol.upper(), {}).get("fetched_at") != before_cache.get(symbol.upper(), {}).get("fetched_at")
        )
        result["fundamentals_cache_hits"] = sum(1 for _, item, _, _ in analysis_pool if item["symbol"].upper() in cache and item["symbol"].upper() in before_cache)
    except Exception as exc:
        result["fundamentals_refresh_errors"] += 1
        result["fundamentals_refresh_error"] = str(exc)

    try:
        funds = float(broker.funds() or settings.reference_capital)
    except Exception:
        funds = settings.reference_capital
    with ThreadPoolExecutor(max_workers=settings.scan_workers) as ex:
        futures = [ex.submit(analyse, broker, item, p, v, funds) for _, item, p, v in analysis_pool]
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
    result["suggested_buy_investment"] = 0.0
    result["suggested_sell_value"] = 0.0
    if result["market_open"]:
        capital = float(settings.reference_capital)
        open_symbols = {r["symbol"] for r in db.recent("positions", 100) if not r.get("closed_at")}
        for c in result["candidates"]:
            if c["symbol"] in open_symbols:
                c["rejection_reason"] = "DUPLICATE_ORDER"
                c["reason"] = "Position already open"
                result["rejections"]["DUPLICATE_ORDER"] = result["rejections"].get("DUPLICATE_ORDER", 0) + 1
                _record_rejection(db, cycle_id, c, mode)
                continue
            if c["decision"] == "BUY" and c["price"] > c["max_chase"]:
                c["decision"] = "NO TRADE"
                c["rejection_reason"] = "ENTRY_EXPIRED"
                c["reason"] = "BUY entry exceeded max-chase price"
                result["rejections"]["ENTRY_EXPIRED"] = result["rejections"].get("ENTRY_EXPIRED", 0) + 1
                _record_rejection(db, cycle_id, c, mode)
                continue
            state = _portfolio_snapshot(db, capital, mode)
            notional = float(c.get("capital_required", 0) or 0)
            if state["open_positions"] >= settings.max_positions:
                why = "POSITION_LIMIT"
            elif state["daily_loss"] >= settings.daily_loss_limit:
                why = "DAILY_LOSS_LIMIT"
            elif state["open_exposure"] + notional > state["deployment_limit"]:
                why = "CAPITAL_DEPLOYMENT_LIMIT"
            else:
                sector_fraction = _sector_exposure(db, capital, mode, c.get("sector", "OTHER"), notional)
                ok, risk_why = risk_gate(float(c.get("rr", 0) or 0), state["daily_loss"], state["open_positions"], sector_fraction)
                why = None if ok else risk_why
            if why:
                c["rejection_reason"] = why
                c["reason"] = f"Execution risk gate: {why}"
                result["rejections"][why] = result["rejections"].get(why, 0) + 1
                _record_rejection(db, cycle_id, c, mode)
                continue
            sid = "SIG-" + uuid.uuid4().hex[:16]
            c["signal_id"] = sid
            db.signal(sid, c["symbol"], c["decision"], c)
            oid = fill(db, c, mode=mode)
            open_symbols.add(c["symbol"])
            result["orders"].append({"order_id": oid, "signal_id": sid, "status": "FILLED", "mode": mode, "symbol": c["symbol"], "side": c["decision"], "quantity": c["quantity"], "price": c["entry"]})
            if c["decision"] == "BUY":
                result["suggested_buy_investment"] += notional
            else:
                result["suggested_sell_value"] += float(c.get("entry", 0) or 0) * int(c.get("quantity", 0) or 0)
    result["suggested_buy_investment"] = round(result["suggested_buy_investment"], 2)
    result["suggested_sell_value"] = round(result["suggested_sell_value"], 2)
    result["execution_gate"] = "LIVE_TEST_SIMULATION" if mode == LIVE_TEST_MODE else "PAPER_MODE"
    try:
        broker_funds = float(funds)
    except Exception:
        broker_funds = float(settings.reference_capital)
    if settings.telegram_token and settings.telegram_chat_id:
        lines = [
            "📊 STOCKMARKET BOT — PAPER CYCLE",
            f"Time: {datetime.now(IST).strftime('%d-%b-%Y %H:%M:%S')} IST",
            f"Universe: {result['stocks_observed']} | Quotes: {result['quotes']} | Deep analysis: {result['deep_analysis_pool']}",
            f"Candidates: {len(result['candidates'])} | Orders: {len(result['orders'])}",
            f"Fundamentals refreshed: {result['fundamentals_refreshed']} | cache hits: {result['fundamentals_cache_hits']}",
            f"Dhan available funds: ₹{broker_funds:,.2f}",
            f"Paper reference capital: ₹{settings.reference_capital:,.2f}",
            f"Paper buy investment: ₹{result['suggested_buy_investment']:,.2f}",
            f"Open positions: {result.get('positions_open', 0)}",
        ]
        if result["candidates"]:
            lines.append("Top candidates: " + ", ".join(f"{c['symbol']} {c['decision']}" for c in result["candidates"][:5]))
        if result["rejections"]:
            top = sorted(result["rejections"].items(), key=lambda x: x[1], reverse=True)[:5]
            lines.append("Rejections: " + ", ".join(f"{k}={v}" for k, v in top))
        telegram("\n".join(lines)[:3900])
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
