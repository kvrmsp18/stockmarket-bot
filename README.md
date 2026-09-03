# NSE/BSE Intraday AI Trading Desk — Paper Trading Build

This repository implements the production-oriented **paper-trading** architecture for the NSE/BSE intraday research and monitoring desk. Source-derived rules are traced in `SOURCE_RULES.md`.

> **Current status:** paper trading only. Live trading is disabled. The system is being validated through GitHub Actions, Dhan market data, source-backed fundamentals, NSE derivatives/pre-open context, deterministic technical/risk gates, and optional AI advisory.

## Non-negotiable safety defaults

- **PAPER TRADING = ON**
- **LIVE TRADING = OFF**
- No arbitrary stock-price cap.
- Minimum configured risk/reward default is **1:3**.
- Trend Score is transparent: **>7 = BULLISH, <4 = BEARISH**.
- SCRAP preserves **SECTORS >15%, COMPANIES >25%, RED FLAGS → REJECTION**.
- Missing/failed external data is represented as **`DATA UNAVAILABLE`** rather than silently converted into a negative score.
- AI is advisory only and cannot override deterministic data, risk, funds, execution, or reconciliation gates.
- Paper sizing uses the configured virtual reference capital of **₹1,000**.
- Paper-validation risk limits are scaled to the ₹1,000 virtual account: **₹20 daily loss limit** and **₹800 maximum single-position exposure**, unless explicitly overridden through configuration/secrets.
- Maximum **2 filled simulated entries per IST trading day**.
- The broker adapter contains a live-order path, but the active configuration keeps live trading disabled.

## Dhan authentication

The active Dhan integration uses a **Dhan Access Token** for authenticated market-data/API requests. The repository configuration supports:

- `DHAN_CLIENT_ID`
- `DHAN_API_KEY`
- `DHAN_API_SECRET`
- `DHAN_PIN`
- `DHAN_TOTP_SECRET`
- `DHAN_ACCESS_TOKEN`
- `DHAN_SECURITY_IDS_JSON`

`DHAN_ACCESS_TOKEN` is preferred when supplied. When it is not supplied, the current paper runtime can fall back to the Dhan PIN + TOTP token-generation path. The API Key + API Secret are retained for the Dhan consent/OAuth authentication path, but an API key/secret pair is **not itself** the bearer credential used on the authenticated market-data endpoints.

The GitHub Actions preflight validates the Dhan credential before the expensive analysis cycle and can validate a mapped market-feed security ID when `DHAN_SECURITY_IDS_JSON` is available. The workflow also avoids unnecessarily regenerating the same short-lived token within a runner. Credentials are stored only in GitHub/Streamlit secrets and are never committed to source.

## Market-data and research pipeline

```text
Complete NSE cash-equity universe
        ↓
Dhan bulk market observation
        ↓
Data-quality / authentication gate
        ↓
Full-universe ranking
        ↓
Dynamic rotating deep-analysis pool
        ↓
5-minute candles / technical structure
        ↓
SCRAP + fundamentals + valuation
        ↓
Buffett + Jhunjhunwala + Lynch + 100 Baggers + CANSLIM research
        ↓
NSE pre-open / breadth context
        ↓
NSE derivatives / OI-spurt context
        ↓
Event & news risk confirmation layer
        ↓
Optional AI advisory / consensus
        ↓
Transparent score + setup validation
        ↓
Entry / Stop / Target / R:R
        ↓
Deterministic risk / funds / liquidity / portfolio gates
        ↓
Paper execution
        ↓
Position monitoring / STOP / TARGET / EOD square-off
        ↓
P&L / journal / reconciliation / reporting / Telegram
```

### Full-universe observation, bounded deep analysis

The monitor observes the complete configured NSE cash-equity universe through a bulk quote stage. Expensive historical candles and research are then limited to a **dynamic rotating shortlist** selected from the full quoted universe. This avoids maintaining a permanent hard-coded stock whitelist while keeping each five-minute scheduled cycle within a practical execution budget.

### NSE pre-open context

`intraday_bot/nse_preopen.py` is designed to consume NSE pre-open market data and normalize opening evidence such as indicative price, previous close, percentage change, and breadth. It classifies the opening environment as bullish, bearish, or mixed when the source data supports that classification.

Pre-open information is **context only**. It is not a standalone trade trigger.

### NSE derivatives / OI context

`intraday_bot/nse_fo.py` consumes NSE OI-spurt/derivatives context when the source feed is reachable. It normalizes source-returned OI, volume and derivatives-value fields and can classify price/OI combinations such as:

- `LONG_BUILDUP`
- `SHORT_BUILDUP`
- `SHORT_COVERING`
- `LONG_UNWINDING`
- `UNKNOWN`

OI context is a secondary confirmation/research layer and does **not** independently create a trade.

### Fundamentals and valuation

The research layer uses the persisted fundamentals cache and source-backed provider pipeline. Missing fundamentals remain `DATA UNAVAILABLE`. The design supports Twelve Data where the configured plan/endpoints provide the required information and a Yahoo Finance/yfinance fallback where appropriate.

Fundamental and valuation evidence is research context, not a substitute for the intraday technical setup. Financial-sector companies also require sector-aware interpretation because generic industrial ratios such as ROCE/debt-to-equity can be misleading for banks and other financial-services businesses.

### Framework research

The research engine incorporates evidence from the requested long-term investing frameworks:

- **Warren Buffett** — quality, economics, durability and valuation evidence.
- **Rakesh Jhunjhunwala** — growth, operating leverage, management/business conviction and scalable opportunity.
- **Peter Lynch** — growth relative to valuation and business-category characteristics.
- **100 Baggers** — long-duration compounding characteristics and business quality.
- **CANSLIM / William O'Neil** — growth, earnings/momentum and market-leadership evidence.

These frameworks are used as **research/conviction inputs**, not as standalone intraday entry signals.

### Event & News Risk layer

The intended event/news layer is a **secondary confirmation and risk-control component**. News is not allowed to override deterministic technical/risk rules or manufacture a trade when market data is missing. High-impact event/news conditions should increase caution or reject an otherwise marginal setup rather than become an independent buy/sell trigger.

## Runtime implementation

`intraday_bot/runtime.py` is the active end-to-end runtime implementation. `scripts/run_daily_cycle.py` invokes its `run_cycle()` entry point.

The runtime performs, in order, universe loading, quote collection, ranking, bounded deep analysis, technical setup validation, deterministic risk checks, paper execution/position management, state persistence and reporting. It also records rejected signals rather than silently discarding them.

## GitHub Actions runtime

The free paper deployment uses GitHub Actions as the scheduler/heartbeat. The active runtime workflows are:

- `.github/workflows/continuous-monitor.yml` — scheduled monitor/heartbeat and intraday paper cycle.
- `.github/workflows/eod-close.yml` — end-of-day paper-position close/reporting.
- `.github/workflows/export-source-audit.yml` — source validation/audit workflow used to verify the repository and persist a current raw-source audit.

The monitor is scheduled on a five-minute cadence across the week. During the NSE market window it runs the market-data and paper-trading cycle. Outside the market window it records heartbeat state and reports `HEARTBEAT_ONLY`; it does not invent market data or trades.

GitHub Actions scheduling is a **best-effort scheduler**, not a guaranteed real-time process. A true persistent worker remains the production option for later live deployment.

## Validation status

The source-audit workflow currently validates Python compilation and the repository test suite. The latest successful audit run validated:

- `python -m py_compile app.py intraday_bot/*.py scripts/*.py`
- `PYTHONPATH=. pytest -q`
- **52 tests passed**
- Complete source-audit generation and GitHub Contents API persistence succeeded.

This validates the codebase at the audited commit; it does **not** claim that external market-data services are permanently available or that GitHub Actions provides guaranteed five-minute execution.

## Repository layout

- `intraday_bot/config.py` — configuration, credentials and safety defaults
- `intraday_bot/database.py` — SQLite persistence and audit events
- `intraday_bot/brokers.py` — broker abstraction, Dhan integration and paper broker
- `intraday_bot/technical.py` — indicators, Trend Score and intraday setup
- `intraday_bot/research.py` — SCRAP, fundamentals, valuation and framework research
- `intraday_bot/nse_preopen.py` — NSE pre-open/breadth context adapter
- `intraday_bot/nse_fo.py` — NSE derivatives/OI-spurt context adapter
- `intraday_bot/fundamentals_cache.py` — persisted fundamentals cache/refresh layer
- `intraday_bot/fundamentals_provider.py` — source-backed fundamentals provider/fallbacks
- `intraday_bot/ai_advisor.py` — optional AI advisory layer
- `intraday_bot/alerts.py` — Telegram delivery
- `intraday_bot/runtime.py` — bounded end-to-end monitor cycle and execution gates
- `scripts/load_universe.py` — NSE cash-equity universe loader
- `scripts/run_daily_cycle.py` — scheduled cycle entry point
- `scripts/run_eod_close.py` — EOD paper-position close
- `scripts/eod_report.py` — daily paper P&L/report generation
- `scripts/worker.py` — independent always-on worker for a later Linux/VPS deployment
- `app.py` — Streamlit trading desk UI
- `src/` — legacy/compatibility validation layer retained where current tests import it
- `tests/` — automated validation and safety tests
- `SOURCE_RULES.md` — source/engineering rule traceability

## Complete NSE universe

`load_universe.py` downloads Dhan's public security master and keeps the NSE cash-equity universe. When the series column is available, only `EQ` is retained. The loader refuses to continue with a suspiciously small universe rather than silently substituting a fixed stock list.

## Running locally

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python scripts/load_universe.py
python scripts/run_daily_cycle.py
```

For Streamlit:

```bash
streamlit run app.py
```

Credentials should be entered through the deployment's secret/configuration mechanism, never committed to Git.

## Paper-trading operating model

The project is deliberately being validated in paper mode before any consideration of live orders. A simulated entry must pass the technical setup, minimum R:R, funds/position sizing, liquidity, portfolio exposure, duplicate-position and daily risk gates. Positions are monitored for stop, target and end-of-day square-off, with P&L and charges recorded in the ledger.

The ₹1,000 reference account is a **virtual validation account**. It does not represent the user's real Dhan cash balance and does not authorize real-market orders.

## 24/7 free-tier behavior

The intended free-tier design is a five-minute **scheduler/heartbeat**, not a claim that Streamlit Community Cloud is a persistent background worker. During market hours the monitor performs the configured paper cycle; outside market hours it continues heartbeat/state handling and waits for the next valid market window.

For production/live operation later, the architecture should move the persistent worker to a Linux/VPS-style environment and retain the same deterministic safety gates, reconciliation, monitoring and audit trail.
