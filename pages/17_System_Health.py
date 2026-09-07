from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from intraday_bot.config import settings
from intraday_bot.ui_health import evaluate_runtime_health, file_state

ROOT = Path("data")


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


st.set_page_config(page_title="System Health", layout="wide")
st.title("System Health")
st.caption("Evidence-based runtime health. Missing evidence is not treated as PASS, and no synthetic market telemetry is used.")

worker = load_json(ROOT / "worker_heartbeat.json")
scheduler = load_json(ROOT / "scheduler_heartbeat.json")
preflight = load_json(ROOT / "preflight_status.json")
status = load_json(ROOT / "monitor_status.json")

health = evaluate_runtime_health(mode=settings.mode, live_enabled=settings.live_enabled, emergency_stop=settings.emergency_stop, worker=worker, scheduler=scheduler, preflight=preflight, status=status)

c1, c2, c3 = st.columns(3)
c1.metric("Overall", health["status"])
c2.metric("Checks", len(health["checks"]))
c3.metric("Failures", len(health["failed_components"]))

rows = [{"Component": x.get("component"), "Status": x.get("status"), "Reason": x.get("reason"), "Age (sec)": x.get("age_seconds"), "Updated": x.get("updated_at")} for x in health["checks"]]
st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

if health["failed_components"]:
    st.warning("Not fully healthy: " + ", ".join(health["failed_components"]))
else:
    st.success("All persisted runtime safety/health checks passed.")

st.subheader("Runtime evidence")
evidence = [file_state(ROOT / "monitor_status.json"), file_state(ROOT / "worker_heartbeat.json"), file_state(ROOT / "scheduler_heartbeat.json"), file_state(ROOT / "preflight_status.json")]
st.dataframe(pd.DataFrame(evidence), use_container_width=True, hide_index=True)

with st.expander("Raw persisted health evidence"):
    st.json({"worker": worker, "scheduler": scheduler, "preflight": preflight, "monitor_status": status})
