from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time as clock, timezone
from pathlib import Path
from typing import Any

from .brokers import DhanBroker
from .config import IST, settings
from .database import Database
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
    if not p.exists(): return []
    try:
        x = json.loads(p.read_text(encoding="utf-8"))
        return x if isinstance(x, list) else []
    except Exception:
        return []


def fundamentals(symbol: str) -> dict[str, Any]:
    p = Path("data/fundamentals.json")
    if not p.exists(): return {}
    try:
        x = json.loads(p.read_text(encoding="utf-8"))
        return x.get(symbol, {}) if isinstance(x, dict) else {}
    except Exception:
        return {}


def quote(q: dict[str, Any]) -> tuple[float, float, float]:
    o = q.get("ohlc") or q.get("OHLC") or {}
    def n(v: Any) -> float:
        try: return float(v)
        except (TypeError, ValueError): return 0.0
    return n(q.get("last_price", q.get("ltp", q.get("lastPrice")))), n(q.get("prev_close", q.get("previousClose", o.get("close")))), n(q.get("volume", q.get("volumeTraded", q.get("volumeTradedToday"))))


def sector(s: str) -> str:
    groups = {"BANKING":{"HDFCBANK","ICICIBANK","SBIN","KOTAKBANK","AXISBANK"},"IT":{"TCS","INFY","WIPRO","HCLTECH","TECHM"},"ENERGY":{"RELIANCE","ONGC","NTPC","POWERGRID","COALINDIA"},"AUTO":{"MARUTI","M&M","EICHERMOT","TATAMOTORS"},"PHARMA":{"SUNPHARMA","CIPLA","DRREDDY"},"METALS":{"TATASTEEL","HINDALCO","JSWSTEEL"},"FMCG":{"ITC","HINDUNILVR"}}
    return next((k for k, v in groups.items() if s in v), "OTHER")


def analyse(broker, item, price: float, volume: float, funds: float) -> dict[str, Any]:
    symbol = item["symbol"]
    f = fundamentals(symbol)
    bundle = research_bundle(symbol, f)
    scrap = scrap_analysis(symbol, f)
    base: dict[str, Any] = {"symbol": symbol, "sector": sector(symbol), "theme": "UNKNOWN", "price": price, "decision": "NO TRADE", "scrap_score": scrap.scrap_score, "fundamental_score": fundamental_score(f), "valuation_score": bundle["valuation_score"], "conviction_score": bundle["frameworks"]["overall"], "framework_status": bundle["status"], "framework_agreement": bundle["frameworks"]["agreement"], "frameworks": bundle["frameworks"]["frameworks"], "liquidity_score": 5.0, "volume_score": min(10.0, volume / 1e6), "market_score": 5.0, "news_score": 5.0, "ai_score": 0.0, "ai_consensus": "NOT_RUN"}
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
    base.update({"entry": t["entry"], "entry_zone": [t["entry_low"], t["entry_high"]], "entry_low": t["entry_low"], "entry_high": t["entry_high"], "max_chase": t["max_chase"], "stop": t["stop"], "target": t["target"], "rr": t["rr"], "trend_score": t["trend_score"], "technical_score": t["technical_score"], "trend_state": t["trend_state"], "research": bundle})
    if t["direction"] == "HOLD":
        base.update(reason="No valid intraday direction", rejection_reason="TECHNICAL_REJECTION")
        return base
    ok, why = risk_gate(t["rr"], 0, 0, 0)
    if not ok:
        base.update(reason="Risk gate failed", rejection_reason=why)
        return base
    size = position_size(t["entry"], t["stop"], t["target"], settings.reference_capital, funds, liquidity_qty=max(1, int(volume / 1000) if volume else 1))
    if not size.quantity:
        base.update(reason="No safe affordable quantity", rejection_reason="INSUFFICIENT_FUNDS")
        return base
    base.update({"decision": t["direction"], "quantity": size.quantity, "capital_required": size.capital_required, "max_risk": size.max_risk, "potential_reward": size.potential_reward, "overall_score": t["trend_score"] * .35 + base["fundamental_score"] * .10 + base["conviction_score"] * .10 + base["volume_score"] * .10 + 5 * .35, "reason": f"Trend={t['trend_state']}; R:R={t['rr']:.2f}; Frameworks={bundle['frameworks']['agreement']}"})
    return base


def _charges(turnover: float) -> float:
    brokerage = min(40.0, turnover * .0003); stt = turnover * .00025; exchange = turnover * .0000307; sebi = turnover / 10_000_000 * 10; stamp = turnover * .00003; gst = (brokerage + exchange + sebi) * .18
    return brokerage + stt + exchange + sebi + stamp + gst


def _manage_positions(db: Database, qmap: dict[str, dict[str, Any]], uni: list[dict[str, Any]]) -> None:
    by_symbol = {x["symbol"]: x for x in uni}; now_utc = datetime.now(timezone.utc).isoformat(); now_ist = datetime.now(IST)
    with db.connect() as con:
        rows = con.execute("SELECT * FROM positions WHERE closed_at IS NULL").fetchall()
        for p in rows:
            item = by_symbol.get(p["symbol"])
            if not item: continue
            price, _, _ = quote(qmap.get(str(item.get("security_id")), {}))
            if price <= 0: continue
            side = p["side"]; reason = None
            if side == "BUY" and price <= p["stop"]: reason = "STOP_LOSS"
            elif side == "BUY" and price >= p["target"]: reason = "TARGET"
            elif side == "SELL" and price >= p["stop"]: reason = "STOP_LOSS"
            elif side == "SELL" and price <= p["target"]: reason = "TARGET"
            if now_ist.time() >= clock(settings.square_off_hour, settings.square_off_minute): reason = "EOD_SQUARE_OFF"
            con.execute("UPDATE positions SET current_price=? WHERE position_id=?", (price, p["position_id"]))
            if not reason: continue
            gross = (price - p["entry_price"]) * p["quantity"] * (1 if side == "BUY" else -1); charges = _charges((price + p["entry_price"]) * p["quantity"]); net = gross - charges; trade_id = "TRD-" + uuid.uuid4().hex[:16]
            con.execute("UPDATE positions SET current_price=?,closed_at=?,pnl=? WHERE position_id=?", (price, now_utc, net, p["position_id"]))
            con.execute("INSERT INTO trades(trade_id,signal_id,symbol,mode,side,quantity,entry_price,exit_price,gross_pnl,charges,net_pnl,exit_reason,opened_at,closed_at,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (trade_id, None, p["symbol"], p["mode"], side, p["quantity"], p["entry_price"], price, gross, charges, net, reason, p["opened_at"], now_utc, p["payload"]))
            db.event("execution", "INFO", "POSITION_CLOSED", {"trade_id": trade_id, "net_pnl": net, "exit_reason": reason}, p["symbol"], p["mode"])


def _record_rejection(db: Database, cycle_id: str, candidate: dict[str, Any], mode: str) -> None:
    payload = dict(candidate); payload["cycle_id"] = cycle_id; payload["record_type"] = "REJECTED_SIGNAL"
    db.event("strategy", "INFO", "SIGNAL_REJECTED", payload, candidate.get("symbol"), mode)


def run_cycle(mode: str = PAPER_MODE) -> dict[str, Any]:
    """Run one cycle. LIVE_TEST uses real market data and simulated fills only.

    It deliberately never calls the Dhan order endpoint. Real LIVE execution remains
    gated separately by the broker/config safety controls.
    """
    mode = str(mode or PAPER_MODE).upper()
    if mode not in {PAPER_MODE, LIVE_TEST_MODE}:
        raise ValueError("mode must be PAPER or LIVE_TEST")
    start = time.monotonic(); db = Database(); cycle_id = uuid.uuid4().hex
    result: dict[str, Any] = {"cycle_id": cycle_id, "started_at": datetime.now(timezone.utc).isoformat(), "mode": mode, "market_open": market_open(), "stocks_observed": 0, "quotes": 0, "candidates": [], "rejections": {}, "rejection_details": [], "orders": [], "errors": [], "suggested_buy_investment": 0.0, "suggested_sell_value": 0.0}
    db.event("engine", "INFO", "CYCLE_START", result)
    uni = universe(); result["stocks_observed"] = len(uni)
    if not uni: result["errors"].append("DATA_UNAVAILABLE: empty universe"); return finish(result, start, db)
    broker = DhanBroker()
    try: qmap = broker.bulk_quotes(uni); result["quotes"] = len(qmap)
    except Exception as exc: result["errors"].append("DATA_ERROR: " + str(exc)); db.event("market_data", "ERROR", "DATA_ERROR", {"error": str(exc)}, mode=mode); return finish(result, start, db)
    ranked = []
    for item in uni:
        p, prev, v = quote(qmap.get(str(item["security_id"]), {}))
        if p > 0: ranked.append((abs(p / prev - 1) * 100 + min(v / 1e6, 5) if prev else min(v / 1e6, 5), item, p, v))
    ranked.sort(reverse=True, key=lambda x: x[0]); shortlist = ranked[:min(60, len(ranked))]
    try: funds = float(broker.funds() or settings.reference_capital)
    except Exception: funds = settings.reference_capital
    with ThreadPoolExecutor(max_workers=settings.scan_workers) as ex:
        futures = [ex.submit(analyse, broker, item, p, v, funds) for _, item, p, v in shortlist]
        for fut in as_completed(futures):
            try:
                c = fut.result(); db.event("research", "INFO", "FRAMEWORK_ANALYSIS", c.get("research", {}), c.get("symbol"), mode)
                if c["decision"] in {"BUY", "SELL"}: result["candidates"].append(c)
                else:
                    reason = c.get("rejection_reason") or "NO_TRADE"; result["rejections"][reason] = result["rejections"].get(reason, 0) + 1; result["rejection_details"].append(c); _record_rejection(db, cycle_id, c, mode)
            except Exception as exc: result["errors"].append("ANALYSIS_ERROR: " + str(exc))
    result["candidates"].sort(key=lambda x: x.get("overall_score", 0), reverse=True); _manage_positions(db, qmap, uni)
    buys = [x for x in result["candidates"] if x.get("decision") == "BUY"]; sells = [x for x in result["candidates"] if x.get("decision") == "SELL"]
    result["suggested_buy_investment"] = round(sum(float(x.get("capital_required", 0) or 0) for x in buys), 2); result["suggested_sell_value"] = round(sum(float(x.get("entry", 0) or 0) * int(x.get("quantity", 0) or 0) for x in sells), 2)
    if result["market_open"]:
        open_symbols = {r["symbol"] for r in db.recent("positions", 100) if not r.get("closed_at")}
        for c in result["candidates"][:settings.max_positions]:
            if c["symbol"] in open_symbols: db.event("execution", "INFO", "DUPLICATE_ORDER", {"reason":"position already open"}, c["symbol"], mode); continue
            if c["decision"] == "BUY" and c["price"] > c["max_chase"]:
                c["decision"] = "NO TRADE"; c["rejection_reason"] = "ENTRY_EXPIRED"; c["reason"] = "BUY entry exceeded max-chase price"; result["rejections"]["ENTRY_EXPIRED"] = result["rejections"].get("ENTRY_EXPIRED", 0) + 1; _record_rejection(db, cycle_id, c, mode); continue
            sid = "SIG-" + uuid.uuid4().hex[:16]; c["signal_id"] = sid; db.signal(sid, c["symbol"], c["decision"], c); oid = fill(db, c, mode=mode); result["orders"].append({"order_id":oid,"signal_id":sid,"status":"FILLED","mode":mode,"symbol":c["symbol"],"side":c["decision"],"quantity":c["quantity"],"price":c["entry"]})
    result["execution_gate"] = "LIVE_TEST_SIMULATION" if mode == LIVE_TEST_MODE else "PAPER_MODE"
    return finish(result, start, db)


def finish(result: dict[str, Any], start: float, db: Database) -> dict[str, Any]:
    result["duration_seconds"] = round(time.monotonic() - start, 3); result["ended_at"] = datetime.now(IST).isoformat(); result["positions_open"] = int(db.scalar("SELECT COUNT(*) FROM positions WHERE closed_at IS NULL") or 0); result["realized_pnl"] = float(db.scalar("SELECT COALESCE(SUM(net_pnl),0) FROM trades") or 0); result["today_realized_pnl"] = float(db.scalar("SELECT COALESCE(SUM(net_pnl),0) FROM trades WHERE mode IN ('PAPER','LIVE_TEST') AND substr(closed_at,1,10)=?", (datetime.now(IST).date().isoformat(),)) or 0)
    with db.connect() as con: con.execute("INSERT OR REPLACE INTO cycles(cycle_id,started_at,ended_at,status,payload) VALUES(?,?,?,?,?)", (result["cycle_id"], result["started_at"], result["ended_at"], "ERROR" if result.get("errors") else "COMPLETED", json.dumps(result, default=str)))
    Path("data").mkdir(exist_ok=True); Path("data/monitor_status.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8"); db.event("engine", "ERROR" if result.get("errors") else "INFO", "CYCLE_END", result, mode=result.get("mode", PAPER_MODE)); return result
