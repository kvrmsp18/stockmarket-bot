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
- Paper sizing uses the configured virtual reference capital (currently **₹1,000**) and never depends on the real Dhan cash balance.

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
- `src/` — legacy/compatibility validation layer retained because the current validation tests still import it
- `tests/` — automated platform, validation, framework, worker and runtime-safety tests
- `SOURCE_RULES.md` — source/engineering rule traceability
- `.github/workflows/continuous-monitor.yml` — five-minute fallback monitor
- `.github/workflows/eod-close.yml` — scheduled EOD paper close

## Complete NSE universe

`load_universe.py` downloads Dhan's public security master and keeps only NSE cash-equity rows. When the series column is available, only `EQ` is retained. The job refuses to continue with a suspiciously small universe rather than silently substituting a fixed stock list.

## Running locally

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python scripts/load_universe.py
python scripts/run_daily_cycle.py
streamlit run app.py
```

For a paper run using real Dhan market data, configure `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN` and the `DHAN_SECURITY_IDS_JSON` mapping. If credentials are absent, the system reports data unavailability; it does not invent live prices.

## GitHub Actions

The fallback monitor is scheduled every 5 minutes on NSE weekdays, with concurrency protection so overlapping cycles cannot create duplicate work. The job has an eight-minute workflow timeout while the internal cycle budget is four minutes.

GitHub Actions secrets required when using Dhan data:

- `DHAN_CLIENT_ID`
- `DHAN_ACCESS_TOKEN`
- `DHAN_API_KEY` (if required by the selected Dhan API operation)
- `DHAN_SECURITY_IDS_JSON`
- optional `OPENAI_API_KEY`
- optional `ANTHROPIC_API_KEY`
- optional Telegram secrets

`DHAN_LIVE_TRADING_ENABLED` is hard-set to `false` in the monitor workflow.

## Telegram

During a market cycle, the bot can report the Dhan available balance, virtual paper capital, universe/quote counts, candidates, simulated orders and rejection funnel. The EOD job reports daily trades, wins/losses, gross P&L, estimated charges, net P&L, starting/ending paper reference capital and open positions.

## UI

The Streamlit desk contains dashboard, screener, stock/360° view, live charts, P&L/journal/performance, diagnostics, health and settings navigation. It is an interface, not the trading engine. The dashboard synchronizes persisted GitHub state with cache-busting and distinguishes the real trading-cycle heartbeat from the scheduler heartbeat.

## Always-on worker

For a true persistent process, deploy `scripts/worker.py` on an always-on Linux/VPS host using `deploy/stockmarket-worker.service`. Streamlit Community Cloud is the UI layer and is not relied upon to host the background worker. GitHub Actions remains the fallback five-minute scheduler for paper monitoring.

## Dhan / Live trading

Dhan is implemented behind `BrokerInterface`. The adapter supports authentication headers, funds, bulk quotes, intraday history, positions, orders and guarded order submission. Credentials are environment variables only.

Live execution is **not enabled by this deployment**. The eventual live path must additionally satisfy the project's explicit validation, reconciliation, daily-loss, funds, data-freshness, duplicate-order, broker-health and emergency-stop gates before a real order can be submitted.

## Tests

```bash
PYTHONPATH=. pytest -q
```

Tests cover source formulas, SCRAP rejection, Trend Score thresholds, quantity safety, risk/reward gates, framework evidence, validation locking, worker heartbeat persistence and runtime portfolio safety. External broker/AI integrations remain advisory/simulation-only until separately validated.

## Verification standard

The release verification sequence is:

1. source audit
2. syntax/import validation
3. automated tests
4. GitHub Actions workflow validation
5. paper market-data cycle
6. persisted state verification
7. dashboard synchronization verification
8. candidate/rejection funnel verification
9. EOD P&L/report verification
10. Telegram delivery verification when credentials are configured

CI passing alone is not treated as proof of end-to-end correctness.

## No fake features

The UI reports `DATA UNAVAILABLE` when live data is not configured. No synthetic price is labeled live. Paper mode and live mode are separate. AI failure never becomes a BUY. A broker request is never treated as a fill until the broker confirms it. No live order endpoint is enabled by the current deployment.
