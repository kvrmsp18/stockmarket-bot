from __future__ import annotations

import json
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


def _load_baskets() -> dict[str, Any]:
    if not BASKET_PATH.exists():
        return {}
    try:
        data = json.loads(BASKET_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_baskets(data: dict[str, Any]) -> None:
    BASKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = BASKET_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(BASKET_PATH)


def _basket_section() -> None:
    st.subheader("Baskets")
    st.caption("User-defined research baskets only. The Bot does not invent constituents or performance data.")
    saved = _load_baskets()
    with st.form("create_basket"):
        name = st.text_input("Basket name")
        symbols_text = st.text_area("NSE symbols (comma or newline separated)")
        benchmark = st.text_input("Benchmark symbol / label (optional)")
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
    st.write(f"**Constituents:** {', '.join(basket.get('symbols', []))}")
    st.write(f"**Benchmark:** {basket.get('benchmark') or 'Not configured'}")
    st.warning("Historical basket/benchmark performance is shown only when real source price histories are supplied; this page will not synthesize returns.")

    raw_prices = st.text_area("Optional price histories for audit (JSON: {SYMBOL:[old,...,latest]})", key="basket_prices")
    if raw_prices.strip():
        try:
            prices = json.loads(raw_prices)
            if not isinstance(prices, dict):
                raise ValueError("Price history must be a JSON object keyed by symbol.")
            result = basket_return(prices)
            st.write(result)
            bench_text = st.text_input("Optional benchmark return %", key="benchmark_return")
            if bench_text.strip():
                relative = benchmark_relative_return(result.get("basket_return_pct"), float(bench_text))
                st.write(relative)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            st.error(f"Invalid source price input: {exc}")


def _portfolio_section() -> None:
    st.subheader("Portfolio concentration & rebalancing advice")
    positions = _positions()
    if not positions:
        st.info("No open PAPER positions with a valid current price are available. Rebalancing advice is DATA UNAVAILABLE until actual position data exists.")
        return
    result = rebalance_advice(positions)
    st.caption("Advisory only. No order, position or risk setting is changed by this page.")
    a, b, c = st.columns(3)
    a.metric("Open paper positions", len(positions))
    a_value = sum(x["market_value"] for x in positions)
    b.metric("Current paper market value", f"₹{a_value:,.2f}")
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
