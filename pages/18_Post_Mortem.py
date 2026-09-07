from __future__ import annotations

import json
import pandas as pd
import streamlit as st
from intraday_bot.database import Database

st.set_page_config(page_title="Post Mortem", layout="wide")
st.title("Post Mortem")
st.caption("Cycle, rejection and error evidence from the persisted ledger. No hypothetical trades or invented market outcomes are added.")
DB = Database()

def query(sql: str, params=()) -> pd.DataFrame:
    try:
        with DB.connect() as con:
            return pd.DataFrame([dict(row) for row in con.execute(sql, params).fetchall()])
    except Exception as exc:
        st.error(f"DATABASE ERROR: {exc}")
        return pd.DataFrame()

cycles = query("SELECT cycle_id, started_at, ended_at, status, payload FROM cycles ORDER BY started_at DESC LIMIT 100")
events = query("SELECT id, ts, component, severity, event_type, symbol, mode, payload FROM events ORDER BY id DESC LIMIT 500")

if cycles.empty:
    st.info("No persisted cycles yet.")
else:
    latest = cycles.iloc[0]
    try:
        payload = json.loads(latest["payload"] or "{}")
    except Exception:
        payload = {}
    st.subheader("Latest cycle")
    st.json({"cycle_id": latest["cycle_id"], "started_at": latest["started_at"], "ended_at": latest["ended_at"], "status": latest["status"], "cycle_success": payload.get("cycle_success"), "market_open": payload.get("market_open"), "stocks_observed": payload.get("stocks_observed"), "quotes": payload.get("quotes"), "validated_candidate_count": payload.get("validated_candidate_count"), "execution_accepted_candidates": payload.get("execution_accepted_candidates"), "execution_rejected_candidates": payload.get("execution_rejected_candidates")})

if not events.empty:
    st.subheader("Failure / rejection evidence")
    work = events.copy()
    work["severity"] = work["severity"].astype(str).str.upper()
    work["event_type"] = work["event_type"].astype(str).str.upper()
    filtered = work[work["severity"].isin({"ERROR", "CRITICAL", "WARNING"}) | work["event_type"].str.contains("REJECT|FAIL|ERROR", regex=True)]
    if filtered.empty:
        st.success("No persisted ERROR/CRITICAL/WARNING or reject/fail events in the latest 500 events.")
    else:
        rows = []
        for _, row in filtered.iterrows():
            try:
                event_payload = json.loads(row["payload"] or "{}")
            except Exception:
                event_payload = {}
            rows.append({"Time": row["ts"], "Component": row["component"], "Severity": row["severity"], "Event": row["event_type"], "Symbol": row["symbol"], "Mode": row["mode"], "Reason": event_payload.get("execution_rejection_reason", event_payload.get("rejection_reason", event_payload.get("reason", "DATA UNAVAILABLE")))})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.subheader("Recent cycle/rejection events")
    st.dataframe(events.head(100), use_container_width=True, hide_index=True)
