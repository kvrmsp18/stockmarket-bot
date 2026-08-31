# NSE/BSE Intraday AI Trading Desk — New Build

This repository has been replaced with a new production-oriented architecture based on the supplied 128-section specification. The supplied specification is treated as the source of requirements; source-derived rules are traced in `SOURCE_RULES.md`.

## Non-negotiable safety defaults

- **PAPER TRADING = ON**
- **LIVE TRADING = OFF**
- No arbitrary stock-price cap.
- Minimum configured risk/reward defaults to **1:3**.
- Trend Score is transparent and uses source defaults **>7 = BULLISH, <4 = BEARISH**.
- SCRAP preserves **SECTORS >15%, COMPANIES >25%, RED FLAGS → REJECTION**.
- Missing data is `DATA_UNAVAILABLE`, not silently negative.
- AI is advisory only and cannot override deterministic risk, funds, execution or reconciliation gates.

## Architecture

```text
Complete NSE cash-equity universe
        ↓
Bulk market observation (fast)
        ↓
Bounded high-information shortlist
        ↓
5-minute candles / technicals / MTF
        ↓
SCRAP + fundamentals + valuation + conviction
        ↓
AI advisory consensus (optional)
        ↓
Transparent ranking
        ↓
Entry / Stop / Target / R:R
        ↓
Quantity: risk ∧ funds ∧ position ∧ liquidity ∧ broker
        ↓
Risk gate
        ↓
Paper execution / guarded Live adapter
        ↓
Position monitoring / exits
        ↓
P&L / journal / reconciliation / reports
```

The complete universe is observed using a bulk quote stage. Expensive per-symbol candle/research work is bounded to the highest-information shortlist so a 5-minute scheduled cycle does not turn into the 8-minute timeout failure seen in the previous design.

## Repository layout

- `intraday_bot/config.py` — configuration and safety defaults
- `intraday_bot/database.py` — SQLite persistence and audit events
- `intraday_bot/brokers.py` — BrokerInterface, DhanBroker, PaperTradingBroker
- `intraday_bot/technical.py` — indicators, Trend Score, intraday setup
- `intraday_bot/research.py` — SCRAP, fundamentals, valuation, conviction frameworks
- `intraday_bot/runtime_v2.py` — bounded end-to-end monitor cycle
- `scripts/load_universe.py` — complete NSE cash-equity universe loader
- `scripts/run_daily_cycle.py` — scheduled cycle entry point
- `app.py` — Streamlit trading desk UI and live chart interface
- `tests/test_platform.py` — automated core tests
- `SOURCE_RULES.md` — source/engineering rule traceability
- `.github/workflows/continuous-monitor.yml` — 5-minute fallback monitor

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

The monitor is scheduled every 5 minutes during NSE hours, with concurrency protection so overlapping cycles cannot place duplicate work. Each job has a five-minute timeout and the scanner is explicitly bounded.

GitHub Actions secrets required when using Dhan data:

- `DHAN_CLIENT_ID`
- `DHAN_ACCESS_TOKEN`
- `DHAN_API_KEY` (if required by the selected Dhan API operation)
- `DHAN_SECURITY_IDS_JSON`
- optional `OPENAI_API_KEY`
- optional `ANTHROPIC_API_KEY`
- optional Telegram secrets

`DHAN_LIVE_TRADING_ENABLED` is hard-set to `false` in the monitor workflow.

## UI

The Streamlit desk contains dashboard, screener, stock/360° view, live charts, P&L/journal/performance, diagnostics, health and settings navigation. It is an interface, not the trading engine. The backend workflow continues without the browser being open.

## Dhan / Live trading

Dhan is implemented behind `BrokerInterface`. The adapter supports authentication headers, funds, bulk quotes, intraday history, positions, orders and guarded order submission. Credentials are environment variables only.

Live execution is **not enabled by this deployment**. The eventual live path must additionally satisfy the project's explicit validation, reconciliation, daily-loss, funds, data-freshness, duplicate-order, broker-health and emergency-stop gates before a real order can be submitted.

## Tests

```bash
PYTHONPATH=. pytest -q
```

Tests cover source formulas, SCRAP rejection, Trend Score thresholds, quantity safety and risk/reward gates. External broker/AI integrations must also be tested with controlled mocks before live activation.

## No fake features

The UI reports `DATA UNAVAILABLE` when live data is not configured. No synthetic price is labeled live. Paper mode and live mode are separate. AI failure never becomes a BUY. A broker request is never treated as a fill until the broker confirms it.

## Development phases

The implementation follows the supplied phase order: foundation → database → market data → regime/breadth → sector/theme/value migration → SCRAP/fundamentals/valuation → technical/MTF → screening/ranking → AI → strategy → entry/stop/target → risk/quantity/funds/liquidity → paper trading → monitoring/exits → journal/P&L → UI/charts → Dhan → live safety → reconciliation/EOD → backtesting → performance → automation → hardening.
