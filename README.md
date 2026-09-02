# NSE/BSE Intraday AI Trading Desk — New Build

This repository implements the production-oriented paper-trading architecture based on the supplied 128-section specification. Source-derived rules are traced in `SOURCE_RULES.md`.

## Non-negotiable safety defaults

- **PAPER TRADING = ON**
- **LIVE TRADING = OFF**
- No arbitrary stock-price cap.
- Minimum configured risk/reward defaults to **1:3**.
- Trend Score is transparent and uses source defaults **>7 = BULLISH, <4 = BEARISH**.
- SCRAP preserves **SECTORS >15%, COMPANIES >25%, RED FLAGS → REJECTION**.
- Missing data is `DATA_UNAVAILABLE`, not silently negative.
- AI is advisory only and cannot override deterministic risk, funds, data, execution or reconciliation gates.
- Paper sizing uses the configured virtual reference capital (**₹1,000**).
- Paper-validation risk limits are scaled to the ₹1,000 virtual account: **₹20 daily loss limit** and **₹800 maximum single-position exposure**, unless explicitly overridden through secrets.
- Maximum **2 filled simulated entries per IST trading day**.

## Dhan authentication for the current paper phase

The Bot uses the user's longer-valid Dhan credential stored in the `DHAN_API_KEY` GitHub/Streamlit secret together with `DHAN_CLIENT_ID`. `DHAN_ACCESS_TOKEN` is the short-lived 24-hour credential and is intentionally **not used anywhere in the active paper path**.

For the Dhan REST endpoints used by this Bot, the credential value is sent through Dhan's `access-token` authentication header. Therefore, the value placed in `DHAN_API_KEY` must be the Dhan credential that Dhan accepts for that header. A generic API key/secret pair cannot be silently converted into an access token by the Bot.

The previous GitHub Actions failure was **HTTP 401 / error `DH-901`**: `Client ID or user generated access token is invalid or expired.` That run was using `DHAN_ACCESS_TOKEN` because the code gave that secret precedence over `DHAN_API_KEY`. The active configuration has now been changed so `DHAN_API_KEY` is the sole Dhan credential source and the workflow no longer injects `DHAN_ACCESS_TOKEN`.

The Dhan preflight validates account authentication and, when `DHAN_SECURITY_IDS_JSON` contains a mapped security ID, validates the actual market-feed path before the expensive cycle. The market-feed adapter also has an LTP fallback when the quote endpoint itself is unavailable. Credentials remain in GitHub Actions/Streamlit secrets and are never committed to source.

## Architecture

```text
Complete NSE/BSE cash-equity universe
        ↓
Bulk market observation (fast)
        ↓
Bounded high-information rotating shortlist
        ↓
5-minute candles / technicals
        ↓
SCRAP + fundamentals + valuation + conviction
        ↓
Optional AI advisory on top shortlist
        ↓
Transparent ranking
        ↓
Entry / Stop / Target / R:R
        ↓
Quantity: risk ∧ funds ∧ position ∧ liquidity ∧ broker
        ↓
Portfolio deployment / daily-loss / sector / duplicate gates
        ↓
Paper execution / guarded Live adapter
        ↓
Position monitoring / STOP / TARGET / EOD square-off
        ↓
P&L / journal / reconciliation / reports / Telegram
```

The complete universe is observed using a bulk quote stage. Expensive per-symbol candle/research work is bounded to the highest-information rotating shortlist so a scheduled cycle remains within the workflow budget.

## Runtime entry point

`intraday_bot/runtime.py` is the **only active runtime implementation**. `scripts/run_daily_cycle.py` invokes its `run_cycle()` function. The obsolete parallel `runtime_v2.py` implementation has been removed so future fixes cannot accidentally be made to an inactive engine.

## Repository layout

- `intraday_bot/config.py` — configuration and safety defaults
- `intraday_bot/database.py` — SQLite persistence and audit events
- `intraday_bot/brokers.py` — BrokerInterface, DhanBroker, PaperTradingBroker
- `intraday_bot/technical.py` — indicators, Trend Score, intraday setup
- `intraday_bot/research.py` — SCRAP, fundamentals, valuation, Buffett/Jhunjhunwala/Lynch/100 Baggers/CANSLIM conviction evidence
- `intraday_bot/ai_advisor.py` — optional OpenAI/Anthropic advisory layer
- `intraday_bot/alerts.py` — Telegram delivery
- `intraday_bot/runtime.py` — bounded end-to-end monitor cycle and execution gates
- `scripts/load_universe.py` — complete NSE cash-equity universe loader
- `scripts/run_daily_cycle.py` — scheduled cycle entry point
- `scripts/run_eod_close.py` — EOD paper-position close and validation report
- `scripts/eod_report.py` — authoritative daily paper P&L report from the intraday ledger
- `scripts/worker.py` — independent always-on worker for a Linux/VPS deployment later
- `app.py` — Streamlit trading desk UI and live chart interface
- `src/` — legacy/compatibility validation layer retained because current validation tests still import it
- `tests/` — automated platform, validation, framework, worker and runtime-safety tests
- `SOURCE_RULES.md` — source/engineering rule traceability
- `.github/workflows/continuous-monitor.yml` — five-minute scheduled monitor/heartbeat, every day
- `.github/workflows/eod-close.yml` — scheduled EOD paper close
- `.github/workflows/export-source-audit.yml` — complete source audit and validation

## Complete NSE universe

`load_universe.py` downloads Dhan's public security master and keeps only NSE cash-equity rows. When the series column is available, only `EQ` is retained. The job refuses to continue with a suspiciously small universe rather than silently substituting a fixed stock list.

## Running locally

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python scripts/load_universe.py
python scripts/run_daily_cycle.py
```

## 24/7 free-tier behavior

The free paper deployment uses GitHub Actions as a five-minute scheduler/heartbeat rather than pretending that Streamlit Community Cloud is a persistent background worker. The monitor is scheduled every five minutes **seven days a week**. During NSE market hours it runs the complete paper cycle. Outside the market window it continues the scheduler heartbeat and explicitly reports `HEARTBEAT_ONLY`; it does not invent market data or trades. The Streamlit dashboard distinguishes scheduler liveness from a successfully completed market cycle.

This is the correct free-tier architecture for the current paper-validation phase. A persistent Linux worker remains the production option for live trading later.
