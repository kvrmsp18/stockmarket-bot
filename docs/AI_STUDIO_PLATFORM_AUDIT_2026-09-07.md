# AI Studio NSE/BSE Intraday Platform — Integration Audit

**Date:** 2026-09-07  
**Repository:** `kvrmsp18/stockmarket-bot`  
**Status:** NOT APPROVED for production replacement

## Decision

The uploaded `nse_bse-intraday-trading-platform` project is a useful UI/UX reference, but its backend is not safe to promote into the production bot unchanged. The production Python trading engine remains the canonical execution/research engine.

The uploaded project is therefore treated as a **reference/prototype**, not as an executable replacement.

## Critical blockers found

### 1. Market data is fabricated

`server/market_engine.ts` creates OHLCV candles locally and uses `Math.random()` to vary price, high/low and volume. `getLatestQuote()` and technical indicators consume those generated candles. This means the displayed LTP, OHLCV, VWAP, RSI, MACD, ATR, ADX, volume and trend score are not verified NSE/BSE observations.

**Action:** do not deploy this market engine. Real market data must come from a verified provider. The existing production bot already has a read-only DhanHQ market-data adapter and Dhan chart-history provider.

### 2. Market breadth/state contains hardcoded numbers

`server/state.ts` initializes NIFTY 50, BANK NIFTY, Sensex, India VIX and advance/decline values as literal constants. These are not refreshed from a verified market source.

**Action:** platform must show `DATA UNAVAILABLE` until a verified observation exists; never display seeded market numbers as current telemetry.

### 3. Universe fundamentals are hardcoded seed data

`server/nse_universe.ts` embeds base prices, EPS, P/E, ROCE, growth, predictability and profitability for the sample universe.

**Action:** fundamentals must come from persisted/provider-backed source data. Missing fields remain unavailable and must not be converted into negative scores or invented values.

### 4. SCRAP implementation changes source thresholds

The uploaded `scrap_engine.ts` contains the source thresholds but also adds undocumented exceptions such as allowing a company-growth override below the sector threshold and vice versa. It also derives the SOURCE-UNCLEAR formula value from ROCE/EPS even though the formula is explicitly marked unverified.

**Action:** preserve the exact source rule, do not invent exceptions, and keep `R.R = (F.A + W.C) / CASH` isolated as SOURCE-UNCLEAR with no computed value unless the source is validated.

### 5. Backtest page contains fabricated performance

`BacktestPerformanceView.tsx` contains a manually authored equity curve and headline metrics such as 68.4% win rate, 2.84 profit factor and -3.8% drawdown. No historical trade dataset is used to calculate those figures.

**Action:** replace with calculated results from real persisted backtest trades, or display `DATA UNAVAILABLE`.

### 6. AI consensus is not actually three independent model outputs

The UI presents ChatGPT + Claude + Gemini consensus, while the uploaded backend's deterministic fallback constructs verdicts/reasoning locally. A Gemini call exists, but there is no equivalent verified execution path for three independent model analyses.

**Action:** label AI as advisory only and report exactly which provider actually produced an answer. Never present deterministic placeholders as model opinions.

### 7. Live mode safety is insufficient

The uploaded UI has three checkboxes, but the API endpoint only forwards `userAcknowledgedRisk` into the live-mode decision. Broker health, token freshness, market-open status, risk-engine health and emergency-stop state are not all enforced by that endpoint.

**Action:** keep live trading disabled during validation. Any future live enablement must require every safety gate server-side, independently of UI state.

### 8. Paper execution uses hardcoded 5x leverage and a three-position cap

The uploaded strategy engine hardcodes 5x intraday leverage and the state engine stops automatic entries after three active positions. These are implementation choices, not verified universal broker guarantees or the user's unlimited-valid-entry override.

**Action:** use configured broker/product margin only when verified; keep paper capital isolated from real broker funds and do not introduce arbitrary trade-count caps.

## Useful parts worth porting

- Dark/glass dashboard visual language.
- 360-degree stock analysis layout.
- Separate SCRAP, Value Migration, paper trading, journal/rejections, AI advisory and system-health sections.
- Clear PAPER/LIVE safety presentation.
- Explicit rejection ledger.
- Price-tier screener concept for demonstrating zero price-cap support.

## Production-safe integration direction

1. Keep the existing Python trading/research engine and GitHub Actions workflows as the source of truth.
2. Port the useful UI/UX concepts only after binding every displayed value to persisted/verified bot data.
3. Use the existing DhanHQ read-only adapter for live LTP and chart data where configured.
4. Keep verified market-regime and MTF calculations from the production engine.
5. Keep fundamentals/provider provenance and `DATA UNAVAILABLE` semantics.
6. Keep the canonical SCRAP portfolio exposure implementation and the existing CANSLIM context implementation.
7. Keep paper capital isolated at the configured ₹1,000 reference amount and preserve the user's no-daily-trade-count-cap override.
8. Keep live order placement disabled while validation is in progress.
9. Do not import the uploaded `data/bot_state.json` into production state because it contains synthetic/example telemetry.

## Acceptance gate

The uploaded platform is **not production-ready** until all critical blockers above are removed and the resulting build passes compilation, automated tests, source audit, real-data checks and live-order safety checks.
