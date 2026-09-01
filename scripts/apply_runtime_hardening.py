from __future__ import annotations

from pathlib import Path
from textwrap import dedent


RUNTIME = Path("intraday_bot/runtime.py")


def main() -> None:
    s = RUNTIME.read_text(encoding="utf-8")
    s = s.replace("import json\nimport time", "import json\nimport os\nimport time", 1)
    s = s.replace(
        "from .database import Database\nfrom .paper import fill\n",
        "from .database import Database\nfrom .paper import fill\nfrom .ai_advisor import advisory\nfrom .alerts import telegram\n",
        1,
    )

    helpers = dedent('''

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
        """Return target-sector exposure including a pending candidate as capital fraction."""
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
                "framework_agreement"
            )}
            result = advisory(
                "Advisory only. Never override deterministic risk, funds, data, execution or reconciliation gates. "
                "Return concise JSON with score 0-10, decision BUY/SELL/HOLD/NO TRADE, confidence 0-1, positives, negatives, risks.\n"
                + __import__("json").dumps(context, default=str)
            )
            candidate["ai_advisory"] = result
            candidate["ai_consensus"] = "ADVISORY_AVAILABLE" if result.get("status") == "AVAILABLE" else (
                "ADVISORY_ERROR" if result.get("status") == "ERROR" else "NOT_CONFIGURED"
            )
            if result.get("text"):
                candidate["ai_advisory_text"] = str(result["text"])[:4000]
    ''')

    marker = "\ndef run_cycle(mode: str = PAPER_MODE) -> dict[str, Any]:\n"
    if "def _portfolio_snapshot" not in s:
        if marker not in s:
            raise SystemExit("run_cycle marker missing")
        s = s.replace(marker, helpers + marker, 1)

    old = '''    result["candidates"].sort(key=lambda x: x.get("overall_score", 0), reverse=True)\n    _manage_positions(db, qmap, uni)\n    buys = [x for x in result["candidates"] if x.get("decision") == "BUY"]\n    sells = [x for x in result["candidates"] if x.get("decision") == "SELL"]\n    result["suggested_buy_investment"] = round(sum(float(x.get("capital_required", 0) or 0) for x in buys), 2)\n    result["suggested_sell_value"] = round(sum(float(x.get("entry", 0) or 0) * int(x.get("quantity", 0) or 0) for x in sells), 2)\n    if result["market_open"]:\n        open_symbols = {r["symbol"] for r in db.recent("positions", 100) if not r.get("closed_at")}\n        for c in result["candidates"][:settings.max_positions]:\n            if c["symbol"] in open_symbols:\n                db.event("execution", "INFO", "DUPLICATE_ORDER", {"reason": "position already open"}, c["symbol"], mode)\n                continue\n            if c["decision"] == "BUY" and c["price"] > c["max_chase"]:\n                c["decision"] = "NO TRADE"\n                c["rejection_reason"] = "ENTRY_EXPIRED"\n                c["reason"] = "BUY entry exceeded max-chase price"\n                result["rejections"]["ENTRY_EXPIRED"] = result["rejections"].get("ENTRY_EXPIRED", 0) + 1\n                _record_rejection(db, cycle_id, c, mode)\n                continue\n            sid = "SIG-" + uuid.uuid4().hex[:16]\n            c["signal_id"] = sid\n            db.signal(sid, c["symbol"], c["decision"], c)\n            oid = fill(db, c, mode=mode)\n            result["orders"].append({"order_id": oid, "signal_id": sid, "status": "FILLED", "mode": mode, "symbol": c["symbol"], "side": c["decision"], "quantity": c["quantity"], "price": c["entry"]})\n    result["execution_gate"] = "LIVE_TEST_SIMULATION" if mode == LIVE_TEST_MODE else "PAPER_MODE"\n'''
    new = '''    result["candidates"].sort(key=lambda x: x.get("overall_score", 0), reverse=True)\n    _run_ai_advisory(result["candidates"])\n    _manage_positions(db, qmap, uni)\n    result["suggested_buy_investment"] = 0.0\n    result["suggested_sell_value"] = 0.0\n    if result["market_open"]:\n        capital = float(settings.reference_capital)\n        open_symbols = {r["symbol"] for r in db.recent("positions", 100) if not r.get("closed_at")}\n        for c in result["candidates"]:\n            if c["symbol"] in open_symbols:\n                c["rejection_reason"] = "DUPLICATE_ORDER"\n                c["reason"] = "Position already open"\n                result["rejections"]["DUPLICATE_ORDER"] = result["rejections"].get("DUPLICATE_ORDER", 0) + 1\n                _record_rejection(db, cycle_id, c, mode)\n                continue\n            if c["decision"] == "BUY" and c["price"] > c["max_chase"]:\n                c["decision"] = "NO TRADE"\n                c["rejection_reason"] = "ENTRY_EXPIRED"\n                c["reason"] = "BUY entry exceeded max-chase price"\n                result["rejections"]["ENTRY_EXPIRED"] = result["rejections"].get("ENTRY_EXPIRED", 0) + 1\n                _record_rejection(db, cycle_id, c, mode)\n                continue\n            state = _portfolio_snapshot(db, capital, mode)\n            notional = float(c.get("capital_required", 0) or 0)\n            if state["open_positions"] >= settings.max_positions:\n                why = "POSITION_LIMIT"\n            elif state["daily_loss"] >= settings.daily_loss_limit:\n                why = "DAILY_LOSS_LIMIT"\n            elif state["open_exposure"] + notional > state["deployment_limit"]:\n                why = "CAPITAL_DEPLOYMENT_LIMIT"\n            else:\n                sector_fraction = _sector_exposure(db, capital, mode, c.get("sector", "OTHER"), notional)\n                ok, risk_why = risk_gate(float(c.get("rr", 0) or 0), state["daily_loss"], state["open_positions"], sector_fraction)\n                why = None if ok else risk_why\n            if why:\n                c["rejection_reason"] = why\n                c["reason"] = f"Execution risk gate: {why}"\n                result["rejections"][why] = result["rejections"].get(why, 0) + 1\n                _record_rejection(db, cycle_id, c, mode)\n                continue\n            sid = "SIG-" + uuid.uuid4().hex[:16]\n            c["signal_id"] = sid\n            db.signal(sid, c["symbol"], c["decision"], c)\n            oid = fill(db, c, mode=mode)\n            open_symbols.add(c["symbol"])\n            result["orders"].append({"order_id": oid, "signal_id": sid, "status": "FILLED", "mode": mode, "symbol": c["symbol"], "side": c["decision"], "quantity": c["quantity"], "price": c["entry"]})\n            if c["decision"] == "BUY":\n                result["suggested_buy_investment"] += notional\n            else:\n                result["suggested_sell_value"] += float(c.get("entry", 0) or 0) * int(c.get("quantity", 0) or 0)\n    result["suggested_buy_investment"] = round(result["suggested_buy_investment"], 2)\n    result["suggested_sell_value"] = round(result["suggested_sell_value"], 2)\n    result["execution_gate"] = "LIVE_TEST_SIMULATION" if mode == LIVE_TEST_MODE else "PAPER_MODE"\n'''
    if old not in s:
        raise SystemExit("runtime order block not found")
    s = s.replace(old, new, 1)

    anchor = '''    result["execution_gate"] = "LIVE_TEST_SIMULATION" if mode == LIVE_TEST_MODE else "PAPER_MODE"\n    return finish(result, start, db)\n'''
    replacement = '''    result["execution_gate"] = "LIVE_TEST_SIMULATION" if mode == LIVE_TEST_MODE else "PAPER_MODE"\n    try:\n        broker_funds = float(funds)\n    except Exception:\n        broker_funds = float(settings.reference_capital)\n    if settings.telegram_token and settings.telegram_chat_id:\n        lines = [\n            "📊 STOCKMARKET BOT — PAPER CYCLE",\n            f"Time: {datetime.now(IST).strftime('%d-%b-%Y %H:%M:%S')} IST",\n            f"Universe: {result['stocks_observed']} | Quotes: {result['quotes']} | Deep analysis: {result['deep_analysis_pool']}",\n            f"Candidates: {len(result['candidates'])} | Orders: {len(result['orders'])}",\n            f"Dhan available funds: ₹{broker_funds:,.2f}",\n            f"Paper reference capital: ₹{settings.reference_capital:,.2f}",\n            f"Paper buy investment: ₹{result['suggested_buy_investment']:,.2f}",\n            f"Open positions: {result.get('positions_open', 0)}",\n        ]\n        if result["candidates"]:\n            lines.append("Top candidates: " + ", ".join(f"{c['symbol']} {c['decision']}" for c in result["candidates"][:5]))\n        if result["rejections"]:\n            top = sorted(result["rejections"].items(), key=lambda x: x[1], reverse=True)[:5]\n            lines.append("Rejections: " + ", ".join(f"{k}={v}" for k, v in top))\n        telegram("\\n".join(lines)[:3900])\n    return finish(result, start, db)\n'''
    if anchor not in s:
        raise SystemExit("runtime notification anchor missing")
    s = s.replace(anchor, replacement, 1)
    RUNTIME.write_text(s, encoding="utf-8")
    print("Runtime hardening applied")


if __name__ == "__main__":
    main()
