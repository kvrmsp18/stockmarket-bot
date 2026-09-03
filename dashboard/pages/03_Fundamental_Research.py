from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from intraday_bot.fundamentals_provider import fetch_fundamentals
from intraday_bot.research import framework_analysis, fundamental_score, valuation_score


ROOT = Path("data")
CACHE = ROOT / "fundamentals.json"

st.set_page_config(page_title="Fundamental Research", layout="wide")
st.title("📊 Fundamental Research")
st.info(
    "Source-backed company fundamentals for Buffett, Rakesh Jhunjhunwala, Peter Lynch, "
    "100 Baggers and CANSLIM. Missing values remain DATA UNAVAILABLE; nothing is invented."
)

symbol = st.text_input(
    "NSE symbol",
    value="ABCAPITAL",
    placeholder="e.g. ABCAPITAL, RELIANCE, TCS, HDFCBANK",
).strip().upper()

left, right = st.columns([1, 1])
with left:
    fetch_clicked = st.button("🔄 Fetch latest source data", type="primary", use_container_width=True)
with right:
    clear_clicked = st.button("Clear loaded source", use_container_width=True)

if clear_clicked:
    st.session_state.pop("fundamental_source_data", None)
    st.rerun()

if fetch_clicked:
    if not symbol:
        st.warning("Enter an NSE symbol.")
    else:
        with st.spinner(f"Fetching source fundamentals for {symbol}…"):
            try:
                data = fetch_fundamentals(symbol)
            except Exception as exc:
                st.error(str(exc))
            else:
                data["fetched_at"] = datetime.now(timezone.utc).isoformat()
                st.session_state["fundamental_source_data"] = data
                ROOT.mkdir(parents=True, exist_ok=True)
                # Local cache is intentionally informational. It is not committed
                # by the web app and does not silently become trading data.
                CACHE.write_text(json.dumps({symbol: data}, indent=2, default=str), encoding="utf-8")

source = st.session_state.get("fundamental_source_data", {})
if source and source.get("symbol") == symbol:
    st.subheader(f"{symbol} — Source Data")

    ignored = {"missing_provider_fields", "source_status", "raw_statistics", "raw_earnings", "raw_profile"}
    available = {k: v for k, v in source.items() if k not in ignored}
    st.dataframe(
        pd.DataFrame(sorted(available.items()), columns=["Metric", "Value"]),
        use_container_width=True,
        hide_index=True,
    )

    research = {
        key: source[key]
        for key in (
            "profit_growth", "eps_growth", "roce", "roe", "debt_to_equity",
            "predictability", "earnings_quality", "pe", "sector_weight_pct",
            "company_weight_pct", "red_flags"
        )
        if key in source
    }

    st.subheader("Research scores")
    a, b, c = st.columns(3)
    a.metric("Fundamental Score", f"{fundamental_score(research):.2f}/10")
    b.metric("Valuation Score", f"{valuation_score(research):.2f}/10")
    c.metric("Source Fields", str(len(research)))

    bundle = framework_analysis(research)
    rows = []
    for name, item in bundle["frameworks"].items():
        rows.append(
            {
                "Framework": name,
                "Score": item["score"],
                "Confidence": item["confidence"],
                "Positive": ", ".join(item["positive_factors"]) or "—",
                "Negative": ", ".join(item["negative_factors"]) or "—",
                "Missing": ", ".join(item["missing_data"]) or "—",
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.write(
        f"**Overall:** {bundle['overall']} · **Agreement:** {bundle['agreement']} · **Status:** {bundle['status']}"
    )

    missing = source.get("missing_provider_fields", [])
    if missing:
        st.warning("Provider did not supply: " + ", ".join(missing))
    else:
        st.success("All eight company-fundamental fields required by the five frameworks are source-backed.")

    st.caption(
        "Relative strength and market trend are intentionally not derived from company fundamentals; "
        "they will be supplied by the market/benchmark layer."
    )

    with st.expander("Raw source response"):
        st.json(source)
else:
    st.caption("Enter a symbol and click ‘Fetch latest source data’ to load current provider-backed fundamentals.")
