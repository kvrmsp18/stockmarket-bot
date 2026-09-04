from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from intraday_bot.config import settings
from intraday_bot.database import Database
from intraday_bot.portfolio_advisor import basket_return, benchmark_relative_return, build_basket, rebalance_advice
from intraday_bot.sector_intelligence import membership

DB = Database()
BASKET_PATH = Path("data/baskets.json")
REBALANCE_HISTORY_PATH = Path("data/basket_rebalance_history.json")


def _payload(value: Any) -> dict[str, Any]:
    try:
        obj = json.loads(value) if isinstance(value, str) else value
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _positions() -> list[dict[str, Any]]:
    rows = DB.recent("positions", 1000)
    active = [r for r in rows if not r.get("closed_at") and str(r.get("mode", "")).upper() == "PAPER"]
    try:
        cache = membership()
    except Exception:
        cache = {}
    symbol_sector = cache.get("symbol_sector") or {}
    result = []
    for row in active:
        symbol = str(row.get("symbol") or "").strip().upper()
        qty = int(row.get("quantity") or 0)
        try:
            current = float(row.get("current_price"))
        except (TypeError, ValueError):
            current = 0.0
        if not symbol or qty <= 0 or current <= 0:
            continue
        payload = _payload(row.get("payload"))
        sector = str(payload.get("sector") or symbol_sector.get(symbol) or "UNKNOWN").strip().upper()
        result.append({
            "symbol": symbol,
            "sector": sector,
            "quantity": qty,
            "entry_price": float(row.get("entry_price") or 0),
            "current_price": current,
            "market_value": current * qty,
            "opened_at": row.get("opened_at"),
        })
    return result


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _load_baskets() -> dict[str, Any]:
    data = _load_json(BASKET_PATH)
    return data if isinstance(data, dict) else {}


def _save_baskets(data: dict[str, Any]) -> None:
    _save_json(BASKET_PATH, data)


def _record_rebalance_history(result: dict[str, Any]) -> None:
    history = _load_json(REBALANCE_HISTORY_PATH)
    if not isinstance(history, list):
        history = []
    signature = json.dumps(result, sort_keys=True, default=str)
    if history:
        previous = history[-1].get("result", {}) if isinstance(history[-1], dict) else {}
        if json.dumps(previous, sort_keys=True, default=str) == signature:
            return
    history.append({"timestamp": datetime.now(timezone.utc).isoformat(), "result": result})
    _save_json(REBALANCE_HISTORY_PATH, history[-200:])


def _source_price_history(symbol: str, period: str = "1mo") -> list[float]:
    """Read a real Yahoo Finance close series; never synthesize missing prices."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(f"{symbol}.NS")
        frame = ticker.history(period=period, auto_adjust=False, actions=False)
        if frame is None or frame.empty or "Close" not in frame:
            return []
        values = pd.to_numeric(frame["Close"], errors="coerce").dropna()
        return [float(x) for x in values.tolist() if float(x) > 0]
    except Exception:
        return []


def _benchmark_history(benchmark: str, period: str = "1mo") -> list[float]:
    # Accept a Yahoo-compatible benchmark ticker; ^NSEI is used only when the user explicitly supplies it.
    if benchmark.startswith("^"):
        ticker_name = benchmark
    else:
        ticker_name = benchmark
    try:
        import yfinance as yf
        frame = yf.Ticker(ticker_name).history(period=period, auto_adjust=False, actions=False)
        if frame is None or frame.empty or "Close" not in frame:
            return []
        values = pd.to_numeric(frame["Close"], errors="coerce").dropna()
        return [float(x) for x in values.tolist() if float(x) > 0]
    except Exception:
        return []


def _basket_section() -> None:
    st.subheader("Baskets")
    st.caption("User-defined research baskets only. Constituents and performance come from explicit user input or real source data; nothing is invented.")
    saved = _load_baskets()
    with st.form("create_basket"):
        name = st.text_input("Basket name")
        symbols_text = st.text_area("NSE symbols (comma or newline separated)")
        benchmark = st.text_input("Benchmark symbol, e.g. ^NSEI (optional)")
        submitted = st.form_submit_button("Save basket")
    if submitted:
        symbols = [x.strip() for x in symbols_text.replace(",", "\n").splitlines() if x.strip()]
        try:
            basket = build_basket(name, symbols, benchmark=benchmark or None)
            saved[basket.name] = basket.to_dict()
            _save_baskets(saved)
            st.success(f"Saved basket: {basket.name}")
        except ValueError as exc:
            st.error(str(exc))

    if not saved:
        st.info("No baskets saved yet.")
        return
    selected = st.selectbox("Saved basket", sorted(saved), key="basket_select")
    basket = saved[selected]
    symbols = [str(x).upper() for x in basket.get("symbols", [])]
    benchmark = str(basket.get("benchmark") or "").strip().upper() or None
    st.write(f"**Constituents:** {', '.join(symbols)}")
    st.write(f"**Benchmark:** {benchmark or 'Not configured'}")

    period = st.selectbox("Performance period", ["1mo", "3mo", "6mo", "1y"], index=0, key="basket_period")
    if st.button("Refresh real source performance", key="basket_refresh"):
        histories = {symbol: _source_price_history(symbol, period) for symbol in symbols}
        result = basket_return(histories)
        st.session_state["basket_result"] = result
        if benchmark:
            bench = _benchmark_history(benchmark, period)
            bench_result = basket_return({benchmark: bench})
            st.session_state["basket_benchmark"] = bench_result
        else:
            st.session_state["basket_benchmark"] = None

    result = st.session_state.get("basket_result")
    if not isinstance(result, dict):
        st.info("Click **Refresh real source performance** to retrieve source-backed price histories.")
    else:
        if result.get("status") == "AVAILABLE":
            st.metric("Equal-weight basket return", f"{float(result['basket_return_pct']):.2f}%")
            symbol_df = pd.DataFrame([{"Symbol": k, "Return %": v} for k, v in result.get("symbol_returns_pct", {}).items()])
            st.dataframe(symbol_df, use_container_width=True, hide_index=True)
        else:
            st.warning("Basket performance is DATA UNAVAILABLE because insufficient real price history was returned.")
        unavailable = result.get("unavailable_symbols") or []
        if unavailable:
            st.warning("Price history unavailable for: " + ", ".join(unavailable))
        bench_result = st.session_state.get("basket_benchmark")
        if benchmark and isinstance(bench_result, dict) and bench_result.get("status") == "AVAILABLE":
            relative = benchmark_relative_return(result.get("basket_return_pct"), bench_result.get("basket_return_pct"))
            st.metric("Relative to benchmark", f"{float(relative['relative_return_pct']):.2f}%")
        elif benchmark:
            st.info("Benchmark comparison is DATA UNAVAILABLE because the benchmark price history was unavailable.")

    history = _load_json(REBALANCE_HISTORY_PATH)
    if isinstance(history, list) and history:
        st.markdown("**Rebalance/advisory history**")
        rows = []
        for item in reversed(history[-50:]):
            r = item.get("result", {})
            rows.append({
                "Timestamp": item.get("timestamp"),
                "Status": r.get("status"),
                "Advice count": len(r.get("advice") or []),
                "Reason": r.get("reason"),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _portfolio_section() -> None:
    st.subheader("Portfolio concentration & rebalancing advice")
    positions = _positions()
    if not positions:
        st.info("No open PAPER positions with a valid current price are available. Rebalancing advice is DATA UNAVAILABLE until actual position data exists.")
        return
    result = rebalance_advice(positions)
    _record_rebalance_history(result)
    st.caption("Advisory only. No order, position or risk setting is changed by this page.")
    a, b, c = st.columns(3)
    a.metric("Open paper positions", len(positions))
    value = sum(x["market_value"] for x in positions)
    b.metric("Current paper market value", f"₹{value:,.2f}")
    c.metric("Advisory status", result["status"])
    st.write(result["reason"])

    st.markdown("**Company weights**")
    company_df = pd.DataFrame([{"Symbol": k, "Weight %": v} for k, v in result["company_weights_pct"].items()])
    st.dataframe(company_df, use_container_width=True, hide_index=True)
    st.markdown("**Sector weights**")
    sector_df = pd.DataFrame([{"Sector": k, "Weight %": v} for k, v in result["sector_weights_pct"].items()])
    st.dataframe(sector_df, use_container_width=True, hide_index=True)
    if result["advice"]:
        st.markdown("**Required review items**")
        st.dataframe(pd.DataFrame(result["advice"]), use_container_width=True, hide_index=True)
    else:
        st.success("No concentration breach detected.")

    with st.expander("Actual open PAPER positions used"):
        st.dataframe(pd.DataFrame(positions), use_container_width=True, hide_index=True)


def main() -> None:
    st.set_page_config(page_title="Portfolio Advisory", layout="wide")
    st.title("Portfolio & Baskets")
    st.info(f"PAPER mode · isolated reference capital ₹{settings.reference_capital:,.2f} · live orders remain disabled")
    _portfolio_section()
    st.divider()
    _basket_section()


main()
