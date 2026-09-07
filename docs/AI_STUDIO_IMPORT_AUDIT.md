# AI Studio / `nse_bse-intraday-trading-platform` Import Audit

Date: 2026-09-07
Target repository: `kvrmsp18/stockmarket-bot` (`main`)

## Decision

**Do not replace the active production runtime with the uploaded AI Studio project.**

The uploaded project is a useful UI/reference implementation, but its backend is not safe to activate because its market and research values are synthetic/hard-coded. The existing Python `intraday_bot` runtime remains the authoritative production/paper-trading stack.

## Hard blockers found in the uploaded project

1. **Synthetic market candles**
   - `server/market_engine.ts` implements `generateCandlesForSymbol()`.
   - It starts from a hard-coded `basePrice` and uses `Math.random()` for price drift, highs/lows and volume.
   - `getLatestQuote()` and `calculateTechnicalIndicators()` consume those generated candles.
   - This is fabricated market data and cannot be used for candidate generation, paper fills, P&L, or displayed market telemetry.

2. **Hard-coded fundamental/SCRAP data**
   - `server/nse_universe.ts` embeds EPS, P/E, ROCE, company growth, sector growth, predictability, profitability and other research values.
   - `server/scrap_engine.ts` consumes those values directly.
   - These values are not source-backed at runtime and must not be treated as current fundamentals.

3. **Hard-coded market telemetry**
   - `server/state.ts` initializes NIFTY 50, BANK NIFTY, SENSEX and India VIX values as fixed constants.
   - The UI would therefore be capable of presenting stale/fabricated telemetry as live state.

4. **Synthetic AI consensus**
   - `server/ai_engine.ts` labels GPT/Claude/Gemini verdicts using deterministic rules based on the synthetic Trend Score/SCRAP result.
   - This is not evidence that three independent models actually evaluated the stock.

5. **Automatic paper BUY logic is built on the synthetic inputs**
   - `server/state.ts` can automatically create paper positions from `evaluateTradeSetup()`.
   - Since the quote, technicals and fundamentals are synthetic, those simulated trades would be invalid test evidence.

6. **Backtest screen contains hard-coded performance series**
   - The uploaded `BacktestPerformanceView.tsx` contains a literal equity curve from ₹1,000 to ₹1,485 rather than loading a persisted, reproducible backtest result.
   - It must not be presented as a verified historical backtest.

7. **Several UI panels contain hard-coded investment claims**
   - The uploaded AI Analyst/Thematic Basket/Value Migration/SCRAP screens include fixed company lists, scores or narrative claims.
   - These can be retained only as labels/layout/reference content unless backed by current persisted/source evidence.

## What is safe/useful to retain

- React/Tailwind visual design and navigation concepts.
- PWA install and online-status hooks.
- Dashboard information architecture.
- Stock 360 layout and chart interaction pattern.
- SCRAP / investor-framework presentation layout.
- Value Migration four-pillar presentation.
- Paper-trading journal and rejection-ledger presentation.
- System-health and safety-gate presentation.
- Post-mortem UI concept.

These should consume the existing repository's real persisted state/data rather than the uploaded project's synthetic backend.

## Required replacement architecture

The existing `intraday_bot` Python runtime remains the single source of truth for:

- Dhan market observation and authentication gates
- complete configured NSE cash-equity universe
- real 5-minute and higher-timeframe history
- technical indicators and MTF RSI
- SCRAP and source-backed fundamentals
- Buffett / Jhunjhunwala / Lynch / 100 Baggers / CANSLIM research inputs
- NSE pre-open / derivatives context
- event/news risk
- deterministic risk and portfolio gates
- ₹1,000 isolated paper capital
- paper execution and position monitoring
- EOD reconciliation/reporting
- Telegram alerts and audit events

A future React frontend must be a presentation/API layer over that runtime. It must not create its own quote generator, universe fundamentals, trade engine, paper ledger, or risk engine.

## Acceptance rules for any future frontend/backend import

A component is eligible for production integration only when:

- no synthetic market prices/candles/volume are generated;
- no hard-coded current index values are presented as live;
- no invented fundamentals are used for scoring;
- `DATA UNAVAILABLE` is preserved when source data is unavailable;
- paper P&L is derived from persisted paper fills only;
- manual real-broker purchases are never represented as broker fills unless reconciled;
- AI remains advisory and cannot override deterministic gates;
- no UI control can enable live orders without the active runtime's complete safety gate;
- backtests load reproducible historical inputs/results instead of literal sample numbers.

## Uploaded UI files

The uploaded UI components and hooks were inspected and retained as reference material. The PWA hook correctly handles standalone mode, iOS detection, `beforeinstallprompt`, and installation state. The online-status hook listens for browser `online`/`offline` events.

## Conclusion

**No unsafe AI Studio backend is to be promoted to `main`.** The correct migration is to preserve the existing real-data/risk runtime and selectively port the useful UI/feature presentation after wiring it to that runtime.
