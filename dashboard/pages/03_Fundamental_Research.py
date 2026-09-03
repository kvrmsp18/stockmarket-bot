from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from intraday_bot.fundamentals_provider import fetch_fundamentals
from intraday_bot.research import FRAMEWORK_RULES, framework_analysis, fundamental_score, valuation_score


ROOT = Path("data")

st.set_page_config(page_title="Fundamental Research", layout="wide")
st.title("📊 Fundamental Research")
st.info(
    "Source-backed company fundamentals for the five research frameworks. "
    "Missing values remain DATA UNAVAILABLE; no values are invented."
)

symbol = st.text_input("NSE symbol", value="ABCAPITAL", placeholder="e.g. RELIANCE, TCS, HDFCBANK").strip().upper()

if st.button("Fetch source data", type="primary"):
    if not symbol:
        st.warning("Enter an NSE symbol.")
    else:
        with st.spinner(f"Fetching source fundamentals for {symbol}…"):
            try:
                data = fetch_fundamentals(symbol)
            except Exception as exc:
                st.error(str(exc))
            else:
                st.session_state["fundamental_source_data"] = data

source = st.session_state.get("fundamental_source_data", {})
if source and source.get("symbol") == symbol:
    st.subheader(f"{symbol} — Source Data")
    available = {k: v for k, v in source.items() if k not in {"missing_provider_fields", "source_status"}}
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
    c.metric("Data Status", "AVAILABLE" if research else "DATA UNAVAILABLE")

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
    st.write(f"**Overall:** {bundle['overall']} · **Agreement:** {bundle['agreement']} · **Status:** {bundle['status']}")

    missing = source.get("missing_provider_fields", [])
    if missing:
        st.warning("Provider did not supply: " + ", ".join(missing))

    with st.expander("Raw provider response"):
        st.json(source)
else:
    st.caption("No source data loaded for the selected symbol yet.")
