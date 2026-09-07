from __future__ import annotations
import json
import pandas as pd
import streamlit as st
from intraday_bot.ai import AIEngine
from intraday_bot.database import Database

st.set_page_config(page_title="AI Analyst", layout="wide")
st.title("AI Analyst")
st.caption("Advisory AI only. It receives persisted bot evidence and cannot approve orders, alter deterministic gates, or invent unavailable data.")
DB = Database()

def latest_context() -> dict:
    context = {}
    try:
        with DB.connect() as con:
            row = con.execute("SELECT payload FROM cycles ORDER BY started_at DESC LIMIT 1").fetchone()
            if row:
                payload = json.loads(row["payload"] or "{}")
                if isinstance(payload, dict):
                    context["latest_cycle"] = {k: payload.get(k) for k in ("cycle_id", "market_open", "cycle_success", "stocks_observed", "quotes", "validated_candidate_count", "execution_accepted_candidates", "execution_rejected_candidates", "market_regime", "rejections") if k in payload}
            rows = con.execute("SELECT symbol, decision, payload FROM signals ORDER BY rowid DESC LIMIT 20").fetchall()
            context["recent_signals"] = []
            for row in rows:
                try: payload = json.loads(row["payload"] or "{}")
                except Exception: payload = {}
                context["recent_signals"].append({"symbol": row["symbol"], "decision": row["decision"], "execution_status": payload.get("execution_status"), "execution_rejection_reason": payload.get("execution_rejection_reason"), "trend_score": payload.get("trend_score"), "reason": payload.get("reason")})
    except Exception:
        return {}
    return context

context = latest_context()
prompt = st.text_area("Question", placeholder="Why were recent candidates rejected? What evidence supports the latest recommendation?", height=100)
if st.button("Analyse", type="primary"):
    if not prompt.strip():
        st.warning("Enter a question first.")
    else:
        with st.spinner("Querying configured advisory provider(s)…"):
            result = AIEngine().analyse({"user_question": prompt.strip(), "persisted_bot_context": context})
        if result["status"] == "DATA_UNAVAILABLE":
            st.warning("DATA UNAVAILABLE — no configured AI provider returned a valid advisory response.")
        else:
            st.write(f"**Provider state:** {result['state']} · **Valid providers:** {result['valid_provider_count']}")
            if result["score"] is not None: st.metric("AI advisory score", f"{result['score']:.2f}/10")
            for provider, answer in result["responses"]:
                st.markdown(f"### {provider}")
                if answer.get("status") != "AVAILABLE":
                    st.warning(f"{answer.get('status')}: {answer.get('reason', 'DATA UNAVAILABLE')}")
                    continue
                st.write(f"Decision: {answer.get('decision', 'DATA UNAVAILABLE')} · Confidence: {answer.get('confidence', 'DATA UNAVAILABLE')}")
                c1, c2, c3 = st.columns(3)
                with c1: st.write("**Positives**"); st.write(answer.get("positives", []))
                with c2: st.write("**Negatives**"); st.write(answer.get("negatives", []))
                with c3: st.write("**Risks**"); st.write(answer.get("risks", []))

st.subheader("Persisted evidence supplied to the analyst")
if context:
    if context.get("latest_cycle"): st.dataframe(pd.DataFrame([context["latest_cycle"]]), use_container_width=True, hide_index=True)
    if context.get("recent_signals"): st.dataframe(pd.DataFrame(context["recent_signals"]), use_container_width=True, hide_index=True)
else:
    st.info("DATA UNAVAILABLE — no persisted cycle/signal context is available yet.")
