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
- AI is advisory only and cannot override deterministic risk, funds, execution or reconciliation gates.
- Paper sizing uses the configured virtual reference capital (**₹1,000**).
- Paper-validation risk limits are scaled to the ₹1,000 virtual account: **₹20 daily loss limit** and **₹800 maximum single-position exposure**, unless explicitly overridden through secrets.
- Maximum **2 filled simulated entries per IST trading day**.

## Dhan authentication for the current paper phase

The Bot uses the configured `DHAN_API_KEY` value as the market-data credential and `DHAN_CLIENT_ID` as the client identifier. The short-lived `DHAN_ACCESS_TOKEN` is not required by the current paper-validation path. Credentials remain in GitHub Actions/Streamlit secrets and are never committed to source.

The Dhan preflight runs before the market cycle. If authentication fails, the cycle is marked degraded and no candidates/orders are fabricated.

## Architecture

```text
Complete NSE cash-equity universe
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
- `scripts/worker.py` — independent always-on worker for a Linux/VPS deployment
- `app.py` — Streamlit trading desk UI and live chart interface
- `src/` — legacy/compatibility validation layer retained because current validation tests still import it
- `tests/` — automated platform, validation, framework, worker and runtime-safety tests
- `SOURCE_RULES.md` — source/engineering rule traceability
- `.github/workflows/continuous-monitor.yml` — five-minute scheduled monitor/heartbeat
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

The free deployment uses GitHub Actions as a five-minute scheduler/heartbeat rather than pretending that Streamlit Community Cloud is a persistent background worker. During NSE market hours it runs the complete paper cycle. Outside the market window it continues the scheduler heartbeat without inventing market data or trades. A persistent Linux worker remains the production option for live trading later.
