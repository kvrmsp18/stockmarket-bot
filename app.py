from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from intraday_bot.brokers import DhanBroker
from intraday_bot.config import settings
from intraday_bot.database import Database
from intraday_bot.fundamentals_provider import fetch_fundamentals
from intraday_bot.research import FRAMEWORK_RULES, framework_analysis, fundamental_score, valuation_analysis
from intraday_bot.runtime import PAPER_MODE, run_cycle
from pages.Portfolio_Advisory import main as portfolio_advisory_main


st.set_page_config(page_title="NSE/BSE Intraday AI Trading Desk", layout="wide", initial_sidebar_state="expanded")
DB = Database()
ROOT = Path("data")
STATUS = ROOT / "monitor_status.json"
HB = ROOT / "worker_heartbeat.json"
SHB = ROOT / "scheduler_heartbeat.json"

PAGES = [
    "Dashboard", "AI Prompt Guide", "Deep Research", "Stock Screener", "360° Stock Analysis",
    "Trend Scanner", "Top Bullish", "Top Bearish", "Live Charts", "Portfolio", "Positions",
    "Orders", "Paper Trading", "P&L", "Trade Journal", "Rejected Signals", "System Health", "Settings",
]


def j(path):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}


def sql(q, p=()):
    try:
        with DB.connect() as c:
            return pd.DataFrame([dict(x) for x in c.execute(q, p).fetchall()])
    except Exception as e:
        st.error(f"DATABASE ERROR: {e}")
        return pd.DataFrame()


def events(kind=None, component=None, limit=1000):
    w, p = [], []
    if kind:
        w.append("event_type=?")
        p.append(kind)
    if component:
        w.append("component=?")
        p.append(component)
    where = " WHERE " + " AND ".join(w) if w else ""
    return sql(f"SELECT id,ts,component,severity,event_type,symbol,mode,payload FROM events{where} ORDER BY id DESC LIMIT ?", tuple(p + [limit]))


def flat(df):
    if df.empty:
        return df
    out = []
    for _, r in df.iterrows():
        try:
            x = json.loads(r.payload)
        except Exception:
            x = {"raw_payload": r.payload}
        if not isinstance(x, dict):
            x = {"raw_payload": x}
        z = {k: r[k] for k in ["id", "ts", "component", "severity", "event_type", "symbol", "mode"]}
        z.update(x)
        out.append(z)
    return pd.DataFrame(out)


def sync(force=False):
    now = time.time()
    last = st.session_state.get("sync", 0.0)
    if not force and now - last < 60:
        return bool(st.session_state.get("sync_ok", True))
    ok = True
    failures = []
    base = "https://raw.githubusercontent.com/kvrmsp18/stockmarket-bot/main/data/"
    for name, path in {"trading.db": ROOT / "trading.db", "monitor_status.json": STATUS, "worker_heartbeat.json": HB, "scheduler_heartbeat.json": SHB}.items():
        tmp = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            request = Request(base + name + f"?t={int(now * 1000)}", headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
            with urlopen(request, timeout=15) as response:
                data = response.read()
            fd, tmp = tempfile.mkstemp(dir=str(path.parent))
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except Exception as exc:
            ok = False
            failures.append(f"{name}: {exc}")
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
    st.session_state.sync = now
    st.session_state.sync_ok = ok
    st.session_state.sync_error = "; ".join(failures)
    st.session_state.sync_at = datetime.now(timezone.utc).isoformat()
    return ok


def heartbeat_age_seconds(payload):
    value = payload.get("updated_at") if isinstance(payload, dict) else None
    if not value:
        return float("inf")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return float("inf")


def verified_candidates(status: dict, heartbeat: dict) -> pd.DataFrame:
    """Return validated research recommendations from a recent successful market cycle."""
    if heartbeat.get("cycle_success") is not True:
        return pd.DataFrame()
    if heartbeat.get("market_open") is not True:
        return pd.DataFrame()
    if heartbeat.get("state") != "RUNNING":
        return pd.DataFrame()
    if heartbeat_age_seconds(heartbeat) > 900:
        return pd.DataFrame()
    rows = status.get("recommendations", status.get("candidates", []))
    return pd.DataFrame(rows) if isinstance(rows, list) else pd.DataFrame()


def latest_status() -> dict:
    """Use monitor_status when valid, otherwise recover the latest cycle ledger."""
    status = j(STATUS)
    if isinstance(status, dict) and status.get("cycle_id"):
        return status
    try:
        with DB.connect() as con:
            row = con.execute("SELECT payload FROM cycles ORDER BY started_at DESC LIMIT 1").fetchone()
        if row:
            payload = json.loads(row["payload"] or "{}")
            if isinstance(payload, dict):
                payload["_recovered_from_cycle_ledger"] = True
                return payload
    except Exception:
        pass
    return status if isinstance(status, dict) else {}


def rejection_breakdown(status: dict) -> pd.DataFrame:
    raw = status.get("rejections") or {}
    rows = []
    if isinstance(raw, dict):
        for reason, count in raw.items():
            try:
                n = int(count)
            except (TypeError, ValueError):
                n = 0
            rows.append({"Gate / rejection reason": str(reason), "Count": n})
    return pd.DataFrame(rows).sort_values("Count", ascending=False) if rows else pd.DataFrame(columns=["Gate / rejection reason", "Count"])


def near_misses(status: dict) -> pd.DataFrame:
    rows = status.get("rejection_details") or []
    if not isinstance(rows, list):
        return pd.DataFrame()
    out = []
    for x in rows:
        if not isinstance(x, dict):
            continue
        out.append({
            "Symbol": x.get("symbol"),
            "Decision": x.get("decision"),
            "Rejection": x.get("execution_rejection_reason", x.get("rejection_reason")),
            "Trend": x.get("trend_score"),
            "Reason": x.get("reason"),
        })
    df = pd.DataFrame(out)
    if not df.empty:
        df["Trend"] = pd.to_numeric(df["Trend"], errors="coerce")
        df = df.sort_values("Trend", ascending=False, na_position="last")
    return df


def _as_payload(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _first_value(mapping: dict, *keys):
    for key in keys:
        if key in mapping and mapping[key] not in (None, "", []):
            return mapping[key]
    return None


def _display_score(value, available=True):
    if not available or value is None:
        return "DATA UNAVAILABLE"
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return "DATA UNAVAILABLE"


def _latest_context(context_df: pd.DataFrame) -> dict[str, dict]:
    latest = {}
    if context_df.empty:
        return latest
    work = context_df.copy()
    if "ts" not in work.columns:
        return latest
    work["_ts"] = pd.to_datetime(work["ts"], errors="coerce", utc=True)
    work = work.sort_values(["_ts", "id"], ascending=[False, False], na_position="last")
    for _, raw in work.iterrows():
        payload = _as_payload(raw.get("payload"))
        symbol = str(_first_value(payload, "symbol") or raw.get("symbol") or "").strip().upper()
        if symbol and symbol not in latest:
            latest[symbol] = payload
    return latest


def _research_screener_rows(df: pd.DataFrame, context_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Normalize research plus persisted signal/rejection context into one row per stock."""
    if df.empty and (context_df is None or context_df.empty):
        return pd.DataFrame()
    research_latest = {}
    if not df.empty:
        work = df.copy()
        work["_ts"] = pd.to_datetime(work["ts"], errors="coerce", utc=True)
        work = work.sort_values(["_ts", "id"], ascending=[False, False], na_position="last")
        for _, raw in work.iterrows():
            payload = _as_payload(raw.get("payload"))
            symbol = str(_first_value(payload, "symbol") or raw.get("symbol") or "").strip().upper()
            if symbol and symbol not in research_latest:
                research_latest[symbol] = (payload, raw)
    context_latest = _latest_context(context_df if context_df is not None else pd.DataFrame())
    symbols = sorted(set(research_latest) | set(context_latest))
    rows = []
    for symbol in symbols:
        payload, raw = research_latest.get(symbol, ({}, None))
        candidate = context_latest.get(symbol, {})
        merged_research = _as_payload(candidate.get("research"))
        research_source = dict(payload)
        if merged_research:
            research_source.update(merged_research)
        for key in ("fundamental_score", "valuation_score", "status", "source_status", "provider", "scrap", "frameworks", "valuation"):
            if key not in research_source and key in candidate:
                research_source[key] = candidate[key]
        status = str(_first_value(research_source, "status", "research_status", "source_status") or "DATA UNAVAILABLE").upper()
        source = str(_first_value(research_source, "provider", "source") or "")
        scrap = _as_payload(research_source.get("scrap"))
        scrap_status = str(_first_value(scrap, "status") or "").upper()
        if status == "DATA UNAVAILABLE" and scrap_status:
            status = scrap_status
        sector = _first_value(candidate, "sector") or _first_value(research_source, "sector")
        theme = _first_value(candidate, "theme") or _first_value(research_source, "theme")
        decision = _first_value(candidate, "decision", "action")
        rejection = _first_value(candidate, "execution_rejection_reason", "rejection_reason", "reason")
        execution = _first_value(candidate, "execution_status")
        trend = _first_value(candidate, "trend_score")
        price = _first_value(candidate, "price", "ltp", "last_price", "close")
        scrap_score = _first_value(research_source, "scrap_score")
        if scrap_score is None:
            scrap_score = _first_value(scrap, "scrap_score", "score")
        fundamentals = _as_payload(research_source.get("fundamentals"))
        valuation = _as_payload(research_source.get("valuation"))
        fundamental = _first_value(research_source, "fundamental_score")
        valuation_score = _first_value(research_source, "valuation_score")
        if fundamental is None:
            fundamental = _first_value(fundamentals, "fundamental_score", "score")
        if valuation_score is None:
            valuation_score = _first_value(valuation, "valuation_score", "score")
        conviction = _first_value(candidate, "conviction_score")
        if conviction is None:
            frameworks = _as_payload(research_source.get("frameworks"))
            conviction = _first_value(frameworks, "overall")
        score_available = status not in {"DATA UNAVAILABLE", "UNKNOWN", ""}
        if not score_available:
            fundamental = valuation_score = scrap_score = conviction = None
        rows.append({
            "Symbol": symbol,
            "Price": _display_score(price, price is not None),
            "Sector": str(sector) if sector not in (None, "", "UNKNOWN") else "DATA UNAVAILABLE",
            "Theme": str(theme) if theme not in (None, "", "UNKNOWN") else "DATA UNAVAILABLE",
            "Direction": str(decision).upper() if decision is not None else "DATA UNAVAILABLE",
            "Execution Status": str(execution).upper() if execution is not None else "NOT_RECORDED",
            "Execution Rejection": str(rejection) if rejection is not None else "—",
            "Trend Score": _display_score(trend, trend is not None),
            "SCRAP Score": _display_score(scrap_score, score_available and scrap_score is not None),
            "Fundamental Score": _display_score(fundamental, score_available and fundamental is not None),
            "Valuation Score": _display_score(valuation_score, score_available and valuation_score is not None),
            "Conviction": _display_score(conviction, score_available and conviction is not None),
            "Research Status": status,
            "Provider": source or "DATA UNAVAILABLE",
            "Rejection Reason": str(rejection) if rejection is not None else "—",
            "Last Updated": (raw.get("ts") if raw is not None else None),
            "_raw_payload": payload or candidate,
        })
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result["_sort_status"] = result["Research Status"].map({"PASS": 0, "AVAILABLE": 0, "PARTIAL": 1, "REJECTED": 2, "DATA UNAVAILABLE": 3}).fillna(4)
    return result.sort_values(["_sort_status", "Symbol"]).drop(columns=["_sort_status"])


def stock_screener():
    header("Stock Screener", "Latest persisted research plus signal/rejection context, normalized to one row per stock. Missing research stays DATA UNAVAILABLE; no generic or fake stock data is substituted.")
    research_raw = events(component="research", limit=3000)
    rejection_raw = events(kind="SIGNAL_REJECTED", limit=3000)
    signals_raw = sql("SELECT rowid AS id, ts, symbol, decision, payload FROM signals ORDER BY rowid DESC LIMIT 3000")
    if not signals_raw.empty:
        signals_raw["component"] = "signals"
    context = pd.concat([rejection_raw, signals_raw], ignore_index=True, sort=False) if not rejection_raw.empty or not signals_raw.empty else pd.DataFrame()
    table = _research_screener_rows(research_raw, context)
    if table.empty:
        st.info("No persisted research or signal records are available yet.")
        return
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        query = st.text_input("Search symbol / sector", key="screener_filter").strip()
    with c2:
        statuses = sorted(table["Research Status"].dropna().astype(str).unique().tolist())
        status_filter = st.multiselect("Research status", statuses, default=[], key="screener_status")
    with c3:
        directions = sorted(table["Direction"].dropna().astype(str).unique().tolist())
        direction_filter = st.multiselect("Direction", directions, default=[], key="screener_direction")
    with c4:
        min_trend = st.number_input("Minimum Trend Score", min_value=-100.0, max_value=100.0, value=-100.0, step=1.0, key="screener_min_trend")
    view = table.copy()
    if query:
        mask = view["Symbol"].str.contains(re.escape(query), case=False, na=False) | view["Sector"].str.contains(re.escape(query), case=False, na=False)
        view = view[mask]
    if status_filter:
        view = view[view["Research Status"].isin(status_filter)]
    if direction_filter:
        view = view[view["Direction"].isin(direction_filter)]
    trend_numeric = pd.to_numeric(view["Trend Score"], errors="coerce")
    view = view[trend_numeric.isna() | (trend_numeric >= min_trend)]
    a, b, c = st.columns(3)
    a.metric("Latest stocks", len(view))
    b.metric("Research available / partial", int(view["Research Status"].isin(["PASS", "AVAILABLE", "PARTIAL"]).sum()))
    c.metric("Research unavailable", int((view["Research Status"] == "DATA UNAVAILABLE").sum()))
    display = view.drop(columns=["_raw_payload"], errors="ignore")
    st.dataframe(display, use_container_width=True, hide_index=True)
    if not view.empty:
        with st.expander("Source evidence for selected row"):
            selected = st.selectbox("Stock", view["Symbol"].tolist(), key="screener_evidence_symbol")
            row = view[view["Symbol"] == selected].iloc[0]
            st.json(row["_raw_payload"])
    st.caption("The screener is a research view, not an order list. A BUY/SELL can be a validated recommendation while still being rejected by portfolio/execution limits for paper entry.")


def header(t, d):
    st.title(t)
    st.info(d)


def dashboard():
    s = latest_status()
    h = j(HB)
    sh = j(SHB)
    header("📈 NSE/BSE Intraday AI Trading Desk", "Observe → Analyse → Filter → Rank → Decide → Validate → Size → Execute → Monitor → Exit → Reconcile. Paper mode is default; AI is advisory only.")
    live_candidates = verified_candidates(s, h)
    a, b, c, d, e, f, g = st.columns(7)
    a.metric("Mode", s.get("mode", "PAPER"))
    b.metric("Universe", s.get("stocks_observed", 0))
    c.metric("Quotes", s.get("quotes", 0))
    d.metric("Recommendations", len(live_candidates))
    e.metric("Paper Accepted", s.get("execution_accepted_candidates", len(s.get("orders", []))))
    f.metric("Open Positions", s.get("positions_open", 0))
    g.metric("Today's P&L", f"₹{float(s.get('today_realized_pnl', 0) or 0):,.2f}")
    cycle_errors = s.get("errors") or []
    age_seconds = heartbeat_age_seconds(h)
    market_is_open = bool(h.get("market_open", s.get("market_open", False)))
    cycle_success = h.get("cycle_success")
    if age_seconds > 900:
        st.error(f"TRADING MONITOR: OFFLINE · last heartbeat {h.get('updated_at', '—')} ({age_seconds / 60:.1f} min ago)")
    elif market_is_open and h.get("state") == "DEGRADED":
        detail = f" — {cycle_errors[0]}" if cycle_errors else f" — {h.get('message', 'cycle completed with errors')}"
        st.warning(f"TRADING ENGINE: DEGRADED · last cycle attempt {h.get('updated_at', '—')}{detail}")
    elif market_is_open and cycle_success is True:
        st.success(f"TRADING ENGINE: ONLINE · last successful market cycle {h.get('updated_at', '—')}")
    elif market_is_open:
        st.warning(f"TRADING ENGINE: NO VERIFIED SUCCESSFUL CYCLE · heartbeat {h.get('updated_at', '—')}")
    else:
        st.info(f"24/7 MONITOR: ACTIVE · NSE MARKET CLOSED · scheduler heartbeat {sh.get('updated_at', '—')}")
    st.write(f"**Scheduler:** {sh.get('state', 'NOT FOUND')} · {sh.get('updated_at', '—')} · market {'OPEN' if sh.get('market_open') else 'CLOSED'}")
    st.caption(f"Data sync: {'OK' if st.session_state.get('sync_ok', True) else 'FAILED'} · {st.session_state.get('sync_at', '—')}")
    if not st.session_state.get("sync_ok", True):
        st.error(f"Dashboard refresh could not retrieve latest persisted state: {st.session_state.get('sync_error', 'unknown error')}")
    st.subheader("Validated trade recommendations")
    if live_candidates.empty:
        if cycle_success is not True:
            reason = cycle_errors[0] if cycle_errors else h.get("message", "No verified successful market cycle")
            st.error(f"No validated recommendations: current market cycle is not verified successful. {reason}")
        elif not market_is_open:
            st.info("No validated recommendations while the NSE market is closed. Previous-cycle recommendations are hidden to prevent stale signals.")
        elif age_seconds > 900:
            st.error("No validated recommendations: the last successful cycle is stale (>15 minutes).")
        else:
            st.warning("No validated recommendations persisted. See the deterministic gate breakdown below for the exact reason.")
    else:
        st.dataframe(live_candidates.drop(columns=["_raw_payload"], errors="ignore"), use_container_width=True, hide_index=True)
    breakdown = rejection_breakdown(s)
    details = near_misses(s)
    st.subheader("Deterministic strategy gate breakdown")
    total_rejected = int(breakdown["Count"].sum()) if not breakdown.empty else 0
    x, y, z, q = st.columns(4)
    x.metric("Validated recommendations", len(live_candidates))
    y.metric("Research/strategy rejected", total_rejected - int(s.get("execution_rejected_candidates", 0) or 0))
    z.metric("Execution rejected", int(s.get("execution_rejected_candidates", 0) or 0))
    q.metric("Paper accepted", int(s.get("execution_accepted_candidates", len(s.get("orders", []))) or 0))
    if breakdown.empty:
        st.info("No persisted rejection breakdown is available for the displayed cycle yet.")
    else:
        st.dataframe(breakdown, use_container_width=True, hide_index=True)
    if not details.empty:
        with st.expander("Near-miss candidates — why each one failed"):
            st.dataframe(details.head(25), use_container_width=True, hide_index=True)


def prompt():
    header("🤖 AI Prompt Guide", "Operational AI contract. Missing data is reported, never invented; AI cannot override deterministic gates.")
    text = f'''NSE/BSE INTRADAY AI ADVISORY CONTRACT
- Never invent missing data; report DATA UNAVAILABLE and missing fields.
- AI is advisory only and cannot create/override an execution gate.
- Deterministic market data, technical, SCRAP, funds, risk, broker and reconciliation controls always win.
- Research frameworks: Buffett, Rakesh Jhunjhunwala, Peter Lynch, 100 Baggers, CANSLIM.
- Bullish threshold: {settings.bullish_threshold}; bearish threshold: {settings.bearish_threshold}; minimum R:R: {settings.min_rr}.
- Paper reference capital: ₹{settings.reference_capital:,.2f}.
- Never expose credentials.'''
    st.code(text, language="text")
    st.download_button("Download Prompt_FINAL current", text, file_name="Prompt_FINAL_current.txt")
    for n, x in FRAMEWORK_RULES.items():
        st.write(f"**{n}:** {x['label']} — {', '.join(x['factors'])}")


def _framework_rows(bundle: dict) -> pd.DataFrame:
    rows = []
    for name, item in bundle.get("frameworks", {}).items():
        rows.append({"Framework": name, "Score": item.get("score", 0), "Confidence": item.get("confidence", 0), "Positive": ", ".join(item.get("positive_factors", [])) or "—", "Negative": ", ".join(item.get("negative_factors", [])) or "—", "Missing": ", ".join(item.get("missing_data", [])) or "—"})
    return pd.DataFrame(rows)


def _valuation_rows(valuation: dict) -> pd.DataFrame:
    return pd.DataFrame([{"Metric": x.get("metric"), "Value": x.get("value"), "Score": x.get("score"), "Basis": x.get("basis")} for x in valuation.get("components", [])])


def frameworks():
    header("🧠 Five Research Frameworks", "The Bot's Deep Research layer. It uses source-backed company fundamentals with provider fallback; missing values remain DATA UNAVAILABLE and are never invented.")
    persisted = events("FRAMEWORK_ANALYSIS")
    persisted_symbols = sorted(persisted.symbol.dropna().astype(str).unique()) if not persisted.empty else []
    default_symbol = persisted_symbols[0] if persisted_symbols else "ABCAPITAL"
    symbol = st.text_input("NSE symbol", value=default_symbol, key="deep_research_symbol").strip().upper()
    c1, c2 = st.columns([1, 3])
    with c1:
        fetch_clicked = st.button("🔄 Refresh source-backed research", type="primary", key="refresh_deep_research")
    with c2:
        st.caption("Refreshes company-source data only. It does not place an order. Research remains advisory; deterministic execution and risk gates remain in force.")
    if fetch_clicked:
        if not symbol:
            st.warning("Enter an NSE symbol first.")
        else:
            with st.spinner(f"Fetching source fundamentals for {symbol}…"):
                try:
                    source = fetch_fundamentals(symbol)
                    research_keys = ("profit_growth", "eps_growth", "roce", "roe", "debt_to_equity", "predictability", "earnings_quality", "pe", "forward_pe", "price_to_sales", "price_to_book", "enterprise_to_revenue", "enterprise_to_ebitda", "peg_ratio", "sector_weight_pct", "company_weight_pct", "red_flags", "sector")
                    research = {key: source[key] for key in research_keys if key in source}
                    bundle = framework_analysis(research)
                    valuation = valuation_analysis(research)
                    snapshot = {"symbol": symbol, "source": source, "research_input": research, "fundamental_score": fundamental_score(research), "valuation_score": valuation["score"], "valuation": valuation, "frameworks": bundle, "fetched_at": datetime.now(timezone.utc).isoformat()}
                    st.session_state.deep_research_source = snapshot
                except Exception as exc:
                    st.error(str(exc))
    live = st.session_state.get("deep_research_source", {})
    if live and live.get("symbol") == symbol:
        source = live.get("source", {})
        bundle = live.get("frameworks", {})
        valuation = live.get("valuation") or {}
        provider = source.get("provider", "unknown")
        status = source.get("source_status", "DATA UNAVAILABLE")
        if status == "AVAILABLE":
            st.success(f"SOURCE-BACKED DATA AVAILABLE · {source.get('source', provider)} · fetched {live.get('fetched_at', '—')}")
        elif status == "PARTIAL":
            st.warning(f"SOURCE-BACKED DATA PARTIAL · {source.get('source', provider)} · fetched {live.get('fetched_at', '—')}")
        else:
            st.error(f"SOURCE DATA UNAVAILABLE · {source.get('source', provider)}")
        fallback_reason = source.get("fallback_reason")
        if fallback_reason:
            st.caption("Twelve Data did not provide the requested data; the Bot automatically used the configured secondary provider.")
        metrics = {k: v for k, v in source.items() if k not in {"missing_provider_fields", "valuation_missing_fields", "source_status", "symbol", "source", "provider", "fallback_reason", "endpoint_errors"}}
        if metrics:
            st.subheader("Source-backed company fundamentals and valuation inputs")
            st.dataframe(pd.DataFrame(sorted(metrics.items()), columns=["Metric", "Value"]), use_container_width=True, hide_index=True)
        a, b, c = st.columns(3)
        a.metric("Fundamental Score", f"{float(live.get('fundamental_score', 0)):.2f}/10")
        b.metric("Valuation Score", f"{float(live.get('valuation_score', 0)):.2f}/10")
        c.metric("Research Status", bundle.get("status", "DATA UNAVAILABLE"))
        if valuation:
            st.caption(f"Valuation status: {valuation.get('status', 'DATA UNAVAILABLE')} · Method: {valuation.get('method', 'NONE')} · {valuation.get('reason', '')}")
            valuation_rows = _valuation_rows(valuation)
            if not valuation_rows.empty:
                st.subheader("Valuation evidence")
                st.dataframe(valuation_rows, use_container_width=True, hide_index=True)
        st.subheader("Framework evidence")
        st.dataframe(_framework_rows(bundle), use_container_width=True, hide_index=True)
        st.write(f"**Overall:** {bundle.get('overall', 0)} · **Agreement:** {bundle.get('agreement', 'DATA UNAVAILABLE')} · **Status:** {bundle.get('status', 'DATA UNAVAILABLE')}")
        missing = source.get("missing_provider_fields", [])
        if missing:
            st.warning("Provider did not supply: " + ", ".join(missing))
        valuation_missing = source.get("valuation_missing_fields", [])
        if valuation_missing:
            st.info("Valuation fields not supplied by provider: " + ", ".join(valuation_missing))
        with st.expander("Raw source-backed research evidence"):
            st.json(live)
        return
    if persisted_symbols:
        matching = persisted[persisted.symbol.astype(str) == symbol]
        if not matching.empty:
            row = matching.iloc[0]
            try:
                p = json.loads(row.payload)
            except Exception:
                p = {}
            f = p.get("frameworks", {})
            st.write(f"Last persisted research: {row.ts} · Overall: {f.get('overall', 'DATA UNAVAILABLE')} · Agreement: {f.get('agreement', 'DATA UNAVAILABLE')}")
            st.dataframe(_framework_rows(f), use_container_width=True, hide_index=True)
            with st.expander("Persisted evidence"):
                st.json(p)
            return
    st.info("No source-backed research loaded yet. Click **Refresh source-backed research** to retrieve the selected NSE company's fundamentals.")


def research_page(page):
    if page == "Deep Research":
        frameworks()
        return
    if page == "Stock Screener":
        stock_screener()
        return
    header(page, f"{page} — persisted Bot results only; no generic/fake stock data is substituted.")
    s = latest_status()
    h = j(HB)
    c = verified_candidates(s, h)
    r = flat(events(component="research"))
    if page in {"Trend Scanner", "Top Bullish", "Top Bearish"}:
        if c.empty:
            st.warning("No verified recommendations in the latest successful market cycle.")
            return
        x = c.copy()
        x["_trend"] = pd.to_numeric(x.get("trend_score", 0), errors="coerce").fillna(0)
        if page == "Top Bullish": x = x[x._trend >= settings.bullish_threshold]
        if page == "Top Bearish": x = x[x._trend < settings.bearish_threshold]
        st.dataframe(x.sort_values("_trend", ascending=False).drop(columns=["_trend"], errors="ignore"), use_container_width=True, hide_index=True)
        return
    if page == "360° Stock Analysis":
        syms = sorted(set(c.get("symbol", pd.Series(dtype=str)).dropna().astype(str)) | set(r.get("symbol", pd.Series(dtype=str)).dropna().astype(str)))
        if not syms:
            st.info("No stock records yet.")
            return
        sym = st.selectbox("Stock", syms, key="stock_360")
        if not c.empty and sym in set(c.symbol.astype(str)):
            st.json(c[c.symbol.astype(str) == sym].iloc[0].to_dict())
        if not r.empty:
            st.dataframe(r[r.symbol.astype(str) == sym], use_container_width=True, hide_index=True)
        frameworks()
        return
    st.info("No dedicated persisted dataset has been written for this view yet.")


def ledger(page):
    table = {"Orders": "orders", "Positions": "positions", "Trade Journal": "trades"}[page]
    df = sql(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 1000")
    header(page, f"Persisted {table} ledger.")
    if not df.empty:
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info(f"No {table} records yet.")


def pnl():
    df = sql("SELECT * FROM trades WHERE mode IN ('PAPER','LIVE_TEST') AND closed_at IS NOT NULL ORDER BY closed_at")
    header("💰 P&L", "Realized simulated-trade P&L including persisted charges.")
    if df.empty:
        st.info("No completed trades yet.")
        return
    df["net_pnl"] = pd.to_numeric(df["net_pnl"], errors="coerce").fillna(0)
    st.metric("Net P&L", f"₹{df.net_pnl.sum():,.2f}")
    st.dataframe(df, use_container_width=True, hide_index=True)


def charts():
    header("📉 Live Charts", "Real Dhan chart data only. Authentication uses the configured Dhan market-data credential.")
    try:
        m = json.loads(settings.dhan_security_ids_json or "{}")
    except Exception:
        m = {}
    if not m:
        st.warning("DHAN_SECURITY_IDS_JSON is not configured.")
        return
    sym = st.selectbox("Symbol", sorted(m), key="chart_symbol")
    tf = st.selectbox("Minutes", [1, 3, 5, 15, 30, 60], index=2, key="chart_tf")
    if st.button("Refresh live chart", key="chart_refresh"):
        try:
            i = m[sym]
            sid = i.get("security_id", i) if isinstance(i, dict) else i
            ex = i.get("exchange_segment", "NSE_EQ") if isinstance(i, dict) else "NSE_EQ"
            df = DhanBroker().history(str(sid), ex, tf)
            if df.empty:
                st.warning("DATA UNAVAILABLE")
                return
            fig = go.Figure(go.Candlestick(x=df.timestamp, open=df.open, high=df.high, low=df.low, close=df.close))
            fig.update_layout(xaxis_rangeslider_visible=False)
            st.plotly_chart(fig, use_container_width=True)
        except Exception as e:
            st.error(f"LIVE DATA ERROR: {e}")


def main():
    sync()
    st.session_state.mode = PAPER_MODE
    st.sidebar.success("🟢 PAPER MODE — SIMULATED ORDERS")
    if st.sidebar.button("🔄 Refresh Dashboard", key="refresh_button"):
        sync(force=True)
        st.rerun()
    if not st.session_state.get("sync_ok", True):
        st.sidebar.error("State sync failed — dashboard may be stale")
    else:
        st.sidebar.caption(f"State sync OK · {st.session_state.get('sync_at', '—')}")
    if st.sidebar.button("▶ Run Analysis", type="primary", key="run_analysis"):
        current = datetime.now(timezone.utc)
        ist_now = current.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Kolkata"))
        is_market_open = ist_now.weekday() < 5 and (ist_now.hour, ist_now.minute, ist_now.second) >= (9, 15, 0) and (ist_now.hour, ist_now.minute, ist_now.second) <= (15, 30, 0)
        if not is_market_open:
            x = {"mode": PAPER_MODE, "market_open": False, "stocks_observed": j(STATUS).get("stocks_observed", 0), "quotes": j(STATUS).get("quotes", 0), "candidates": [], "recommendations": [], "orders": [], "errors": [], "execution_gate": "MARKET_CLOSED", "message": "NSE market is closed. Manual analysis is available during market hours; the 24/7 monitor remains active outside market hours."}
            st.session_state.last_manual_cycle = x
            st.info(x["message"])
        else:
            with st.spinner("Running full deterministic paper cycle…"):
                try:
                    x = run_cycle()
                    st.session_state.last_manual_cycle = x
                except Exception as e:
                    st.error(f"RUN ERROR: {e}")
    nav = st.sidebar.selectbox("Desk", PAGES, index=PAGES.index(st.session_state.get("page", "Dashboard")))
    st.session_state.page = nav
    if nav == "Dashboard":
        dashboard()
    elif nav == "AI Prompt Guide":
        prompt()
    elif nav == "Portfolio":
        portfolio_advisory_main()
    elif nav in {"Deep Research", "Stock Screener", "360° Stock Analysis", "Trend Scanner", "Top Bullish", "Top Bearish"}:
        research_page(nav)
    elif nav in {"Orders", "Positions", "Trade Journal"}:
        ledger(nav)
    elif nav == "P&L":
        pnl()
    elif nav == "Live Charts":
        charts()
    else:
        st.info(f"{nav} view is available for the persisted paper-trading system.")


main()
