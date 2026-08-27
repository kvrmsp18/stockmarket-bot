# NSE/BSE Intraday AI Trading System

AI-powered NSE/BSE intraday research, monitoring, paper-trading, risk-management, Telegram-alert, and guarded broker-execution system.

> **Important:** The project is currently in **research / paper-trading validation**. Live order execution remains disabled by default and must never be enabled merely because automated software tests pass.

---

# Project Goal

Build a continuously monitored NSE/BSE intraday research system that can:

- automatically maintain the research universe;
- evaluate market data and technical conditions;
- apply fundamental-quality checks;
- rank and filter candidates;
- calculate risk-safe and funds-safe quantities;
- maintain paper-trading records and P&L;
- monitor Dhan authentication/funds when configured;
- send only useful operational alerts and end-of-day summaries through Telegram;
- eventually support guarded broker execution after objective validation.

The system is designed so that **research continues even when broker funds are ₹0 or Dhan authentication is temporarily unavailable**. Broker-dependent execution is blocked in those cases.

---

# Project Structure

- `src/` — Core trading/research system code
- `config/` — Configuration files and environment template
- `dashboard/` — Streamlit stock-monitor dashboard
- `tests/` — Automated tests
- `reports/` — Generated validation/report outputs
- `.github/workflows/` — Continuous research, broker checks, Telegram tests, and CI workflows

---

# Current Operating Model

The Bot is intended to operate **unattended during the Indian market session**.

```text
Before / at market open
        ↓
Continuous research monitor starts
        ↓
Market data collection
        ↓
Technical + fundamental screening
        ↓
Quality / ranking / risk gates
        ↓
Candidate selection
        ↓
Paper-trade / research evaluation
        ↓
Repeat during the market session
        ↓
End-of-day validation + summary
        ↓
Telegram EOD summary
```

The continuous monitor is handled by GitHub Actions and is separate from manually opening the Streamlit dashboard.

The dashboard provides visibility into the latest research state; it is **not intended to be the scheduler itself**.

---

# Telegram Notification Policy

Telegram is intentionally kept quiet so that it remains useful rather than becoming a stream of intraday noise.

### Telegram sends:

- **End-of-day trading/research summary**
- **Critical operational issues**, such as:
  - insufficient/low broker funds;
  - Dhan authentication or token-renewal failure;
  - major monitor/research failure;
  - critical data-provider or pipeline failure;
  - other conditions requiring user attention.

### Telegram does NOT send:

- every intraday research cycle;
- every stock scanned;
- every BUY/HOLD/SELL research candidate;
- routine heartbeat messages;
- unnecessary technical logs.

This policy is deliberate: the Bot should monitor continuously while notifying the user only when something meaningful happens.

---

# Project Completion Checklist

This section is the **single source of truth** for project maturity.

## Phase 1 — Research & Safety Foundation

- [x] **1. Research screening pipeline**
- [x] **2. Data-quality isolation**
- [x] **3. Stock-monitor quantity safety**
- [x] **4. Validated OHLCV bars retained for charts**

## Phase 2 — Stock Monitor / Dashboard

- [x] **5. Stock-monitor/dashboard layer**
- [x] **6. Display filtered/selected stocks with individual chart data**
- [x] **7. BUY / SELL / HOLD information**
- [x] **8. Calculated safe quantity**
- [x] **9. Optional manual quantity editing**
- [x] **10. Daily filtered-stock count**
- [x] **11. Dashboard research state and diagnostics**
- [x] **12. Automatic dashboard research initialization**

The dashboard no longer depends on the user manually starting the research engine every time it is opened. Continuous unattended research is handled separately by the monitor workflow.

## Phase 3 — Historical Charts

- [x] **13. Multi-timeframe history**

| Timeframe | Intended resolution |
|---|---|
| 24H | 5-minute bars |
| 7D | 15-minute bars |
| 1M | 1-hour bars |
| 3M | Daily bars |
| 6M | Daily bars |
| 1Y | Daily bars |

- [ ] **14. Final dashboard visual/UI polish**

## Phase 4 — Fundamental & Candidate Quality

- [x] **15. Fundamental-quality research gates**
- [x] **16. Profit-growth check**
- [x] **17. Data-quality and technical-rejection isolation**
- [x] **18. Candidate ranking and risk gates**
- [x] **19. Research diagnostics showing why candidates were rejected**

Fundamental research is not limited to a single metric. Where reliable data is available, the pipeline considers quality factors including:

- profitability;
- profit growth;
- earnings / EPS quality;
- return on equity / capital quality where available;
- valuation context such as P/E where available;
- balance-sheet / financial-quality signals where available;
- data freshness and completeness.

A missing or failed fundamental-data request must be isolated as a **data error** rather than silently treated as a poor company.

## Phase 5 — Paper-Trading Validation

- [x] **20. Freeze generated signals for validation**
- [x] **21. Evaluate paper-trade outcomes from subsequent market data**
- [x] **22. Persistent paper-trading journal foundation**
- [x] **23. Validation performance metrics foundation**
  - Win rate
  - Gross profit / gross loss
  - Net paper P&L
  - Profit factor
  - Average winner / loser
  - Maximum drawdown
  - Signal and outcome counts
- [x] **24. Daily/weekly validation-report foundation**
- [ ] **25. Fully mature recurring daily validation-report pipeline**

## Phase 6 — Continuous Intraday Research Monitor

- [x] **26. Unattended intraday research monitor**
- [x] **27. Recurring market-session monitoring**
- [x] **28. 15-minute research cycle architecture**
- [x] **29. Indian market-session schedule alignment**
- [x] **30. Research continues without opening the dashboard**
- [x] **31. Research continues when Dhan funds are unavailable**
- [x] **32. Operational failure isolation**

The monitor is designed to run repeatedly during the Indian market session rather than performing only one morning scan.

This is important for intraday analysis because a single 9:00 AM snapshot is insufficient. The Bot must refresh market information throughout the session so that later opportunities and changing conditions can be evaluated.

## Phase 7 — Broker Funds, Execution Safety & Realized P&L

- [x] **33. Automatic Dhan broker-funds check**
  - Read available funds automatically.
  - No daily manual capital entry is required.
  - Research continues even when available funds are ₹0.

- [x] **34. Funds-aware quantity selection**
  - Apply risk-safe quantity first.
  - Apply available-funds quantity second.
  - Retain cash reserve.
  - Include estimated charges.
  - If no safe quantity is affordable → no execution.

- [x] **35. Final pre-order balance/risk check**
  - Re-check funds immediately before any eventual real order.
  - Recalculate quantity using the latest price and latest funds.

- [x] **36. Research and P&L continue with insufficient funds**
  - ₹0 broker funds do not stop research, charts, analysis or hypothetical P&L.
  - Real execution remains blocked.

- [x] **37. Estimated net P&L display**
  - Gross P&L
  - Brokerage
  - STT
  - Exchange transaction charges
  - SEBI charges
  - Stamp duty
  - GST
  - Estimated NET P&L

- [ ] **38. Reconcile final realized P&L against broker execution/ledger data**

- [x] **39. Telegram insufficient-funds/authentication alert foundation**

- [x] **40. Guarded Dhan live-order adapter**
  - Dhan order API integration exists.
  - Live execution is disabled by default.
  - `DHAN_LIVE_TRADING_ENABLED=true` is the explicit live switch.
  - The validation gate must still be satisfied before enabling it.

## Phase 8 — Telegram Integration

- [x] **41. Telegram Bot integration**
- [x] **42. Telegram connectivity test**
- [x] **43. End-of-day summary delivery architecture**
- [x] **44. Critical operational alert delivery**
- [x] **45. Quiet-notification policy**

The Telegram integration is intended as the Bot's operational notification channel, not as a replacement for the dashboard.

## Phase 9 — Real-World Validation Gate

- [ ] **46. Run a controlled paper-trading period**
  - Use real market data.
  - Record every eligible signal.
  - Include losses and ambiguous outcomes.
  - Do not cherry-pick successful trades.

- [ ] **47. Produce complete daily reports**

Each trading day should answer:

- How many stocks were scanned?
- How many were filtered out?
- How many became actionable?
- Which signals were produced?
- What entry/stop/target/quantity was calculated?
- What was estimated paper P&L?
- What happened to matured signals?
- Were there data-quality or technical failures?
- Was broker authentication/funds healthy?

- [ ] **48. Produce complete weekly reports**

Each week should answer:

- Total signals
- Wins / losses / open / ambiguous
- Win rate
- Profit factor
- Net paper P&L
- Maximum drawdown
- Average risk/reward
- Performance by stock / signal type where meaningful
- Data-quality failure rate
- Whether the strategy is improving, degrading, or stable

- [ ] **49. Establish objective pass/fail criteria**

Criteria should cover:

- minimum data-quality reliability;
- acceptable signal coverage;
- acceptable drawdown;
- minimum sample size;
- minimum acceptable risk-adjusted performance.

Criteria must be agreed **before** using results to justify live trading.

- [ ] **50. Final end-to-end test**

```text
Market data
    ↓
Continuous research monitor
    ↓
Technical screening
    ↓
Fundamental quality checks
    ↓
Profit-growth / valuation / quality gates
    ↓
Data-quality validation
    ↓
Ranking / candidate selection
    ↓
Risk calculation
    ↓
Paper-trade signal freeze
    ↓
Broker funds check
    ↓
Affordable executable quantity
    ↓
Final pre-order balance/risk check
    ↓
Guarded order execution
    ↓
Position monitoring
    ↓
Target / Stop / Exit
    ↓
Actual executed P&L + charges
    ↓
Daily report
    ↓
Telegram EOD summary / critical alerts
    ↓
Weekly report
    ↓
Long-term validation decision
```

- [ ] **51. Live-trading readiness decision**
  - Live trading is allowed only after the validation period and objective criteria are satisfied.
  - Passing automated tests alone is insufficient.

---

# How We Will Know the System Is Working

We use four validation levels.

### Level A — Software correctness

GitHub Actions / automated tests should pass for the relevant production and validation workflows.

### Level B — Data & pipeline correctness

Monitoring should demonstrate that:

- market data arrives correctly;
- invalid/broken symbols are isolated;
- validated bars are retained;
- technical and fundamental checks behave deterministically;
- profit-growth and other quality gates are applied when reliable data is available;
- signals do not use future information;
- quantities obey risk limits;
- charts match retained market data.

### Level C — Trading-strategy effectiveness

Paper-trading reports must demonstrate performance over a sufficiently large unseen sample.

### Level D — Broker/execution correctness

When live execution is eventually enabled:

- Dhan authentication is healthy;
- token renewal works;
- available funds are read automatically;
- funds are rechecked before every order;
- quantity never exceeds risk/funds limits;
- duplicate orders are prevented;
- target/stop exits are tracked;
- final P&L is reconciled with actual execution data.

Only Levels C + D can justify continuing live trading.

---

# Reporting Plan

```text
DURING EACH TRADING SESSION
        ↓
Continuous unattended research
        ↓
Technical + fundamental quality checks
        ↓
Candidate ranking
        ↓
Freeze eligible paper signals
        ↓
Evaluate matured previous signals
        ↓
Monitor Dhan authentication/funds
        ↓
Repeat on the configured intraday cycle
        ↓
END OF DAY
        ↓
Generate daily validation summary
        ↓
Send concise Telegram EOD report
        ↓
Send Telegram alert only if a critical issue exists
        ↓
Accumulate results
        ↓
EVERY WEEK
        ↓
Generate weekly performance report
        ↓
Review quality + P&L + drawdown + failures
```

The goal is to answer with evidence:

> **Is our stock-selection and intraday research system actually working?**

---

# DhanHQ Integration

The project includes DhanHQ integration for authentication, token renewal, broker-fund checks, market data, positions/orders, and a guarded live-order adapter.

### Environment variables

```text
DHAN_CLIENT_ID=your_dhan_client_id
DHAN_ACCESS_TOKEN=your_current_dhan_access_token
DHAN_TOKEN_FILE=.dhan_access_token.json
DHAN_LIVE_TRADING_ENABLED=false
```

Optional Telegram:

```text
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
```

**Never commit real Dhan or Telegram credentials to GitHub.** `.env` files and the local Dhan token cache must remain ignored.

### Dhan token handling

Dhan access-token validity and token renewal are authentication concerns, not evidence that the research engine should stop.

The project is designed to renew the token when supported and to alert through Telegram when authentication/renewal fails.

If Dhan authentication fails:

1. Telegram sends an operational alert when configured.
2. Research continues where broker authentication is not required.
3. Broker-dependent execution remains blocked.

### Live execution safety

`DHAN_LIVE_TRADING_ENABLED=false` is the default.

Before any eventual live order the Bot must:

1. read latest Dhan funds;
2. use latest market price/data;
3. recalculate risk-safe quantity;
4. recalculate funds-safe quantity;
5. retain required cash reserve;
6. verify quantity is positive and valid;
7. prevent duplicate execution;
8. place the order only when all safety gates pass.

---

# Fundamental Research

The research engine is intended to combine market behaviour with company-quality context rather than relying only on one technical indicator.

Where provider data is available and fresh enough, the research layer should consider:

- P/E and valuation context;
- EPS / earnings quality;
- **profit growth**;
- ROE / ROCE or comparable return metrics where available;
- balance-sheet quality;
- debt / financial-risk context where available;
- liquidity and market-quality information;
- technical trend and momentum;
- volatility and downside risk;
- data freshness and completeness.

### Important data-provider rule

Yahoo Finance is one research-data provider used by the project; it is **not intended to be the only possible source for every type of market/fundamental information**.

If a provider returns an error such as HTTP 401/404 or missing fundamentals, the affected symbol should be marked as a **data error / unavailable fundamental input**, not automatically treated as a bad trading candidate.

The research pipeline must continue processing other symbols when one provider request fails.

---

# Research Diagnostics

The dashboard includes research diagnostics so that rejected stocks can be understood rather than silently disappearing.

Typical statuses include:

- `NO_CANDIDATE` — technical/fundamental/risk conditions did not produce an actionable candidate;
- `DATA_ERROR` — required data could not be retrieved or validated;
- technical rejection;
- quality/fundamental rejection;
- risk rejection.

This distinction is important for measuring whether the strategy is genuinely selective or simply suffering from missing data.

---

# P&L

During research/paper validation, the Bot calculates estimated net P&L using configurable charge assumptions.

The model should account for relevant costs including:

- brokerage;
- STT;
- exchange transaction charges;
- SEBI charges;
- stamp duty;
- GST;
- other configured execution costs where applicable.

When actual broker execution is eventually enabled, actual execution/ledger information must supersede estimates wherever available.

---

# Testing

Run:

```bash
pip install -r requirements.txt
PYTHONPATH=. pytest
```

The automated suite covers the research pipeline, data-quality isolation, fundamental-quality gates, risk/quantity safety, monitor-service wiring, daily multi-stock snapshots, chart retention, multi-timeframe history, Telegram integration, broker checks, and paper-trading validation/reporting behavior.

GitHub Actions also runs the recurring/maintenance workflows used by the project.

Some CI maintenance/approval-guard runs may appear separately from the production research monitor. A red approval-guard run should not be interpreted as evidence that the market-monitoring engine itself is down; investigate the specific workflow and job before taking action.

---

# Current Status — 20 August 2026

**Development + live-market paper-trading observation phase.**

### Operationally implemented

- [x] Bot-managed stock research universe
- [x] Automatic NSE/BSE selection architecture
- [x] Multi-timeframe chart/history support
- [x] Research and data-quality gates
- [x] Fundamental-quality checks
- [x] Profit-growth check
- [x] Candidate ranking and risk gates
- [x] Safe model quantity
- [x] Broker-aware funds checking
- [x] Funds-aware executable quantity calculation
- [x] Estimated net P&L with charges
- [x] Dhan authentication/funds workflow
- [x] Dhan token-renewal handling
- [x] Guarded Dhan order adapter
- [x] Continuous unattended intraday research monitor
- [x] Indian market-session scheduling
- [x] Telegram integration
- [x] Telegram EOD summary architecture
- [x] Telegram critical operational alerts
- [x] Dashboard automatic research initialization
- [x] Research diagnostics
- [x] Safe mode with live execution disabled

### Still required before live trading

- [ ] Complete a sufficiently long controlled paper-trading period
- [ ] Produce and review complete daily/weekly evidence
- [ ] Reconcile final realized P&L against actual broker data
- [ ] Establish objective pass/fail thresholds
- [ ] Complete final end-to-end validation
- [ ] Make a formal live-trading readiness decision

### Current safety state

```text
Research:                  ENABLED
Continuous monitoring:     ENABLED
Paper trading:             ENABLED
Telegram alerts:           ENABLED
Dhan funds monitoring:     ENABLED when configured
Live order execution:      DISABLED
```

The Bot should be allowed to gather real-market evidence before additional strategy changes are made. The next major milestone is **observing actual intraday sessions and reviewing the resulting daily/weekly evidence**, not enabling live trading prematurely.

---

# Safety & Disclaimer

This project is for research, software testing, paper trading, and controlled validation purposes only. It is **not financial advice** and does not guarantee profits.

Never enable live trading solely because the software tests pass or because a short paper-trading sample is profitable. Live trading requires objective evidence, adequate sample size, risk controls, broker reconciliation, and an explicit readiness decision.

---

# Repo Consolidation Note (this file)

This copy of the project was assembled by Claude from the `src/` files shared
in chat, plus two new additions built and verified against them in the same
session:

- `src/validation_gate.py` + `config/validation_criteria.json` — the Phase 9
  pass/fail evaluator (checklist items #46–49). Loads locked criteria, pools
  the weekly `ValidationSummary` records already produced by
  `paper_trading_validation.py`, and refuses to score a period against
  criteria that were locked after that period started.
- `tests/test_validation_gate.py` — 12 tests, run against the real
  `ValidationStore`/`ValidationSummary` classes, not mocks.

Also new, added in the same session, tested against the real code before
being included here:

- `scripts/run_daily_cycle.py` — the entry point GitHub Actions calls every
  15 minutes. Runs one read-only monitor cycle and freezes any newly
  actionable candidate as a paper signal, using the AI-suggested quantity
  (not a dashboard override). Deliberately does *not* freeze a second
  signal for a symbol that already has one open from earlier today — see
  its docstring for why that matters for Phase 9's "no cherry-picking" rule.
  It does **not** close signals at EOD or generate reports; that's a
  separate script, not yet built, run once a day rather than every cycle.
- `.github/workflows/continuous-monitor.yml` — schedules that script during
  NSE/BSE hours, queues (never cancels) overlapping runs so the journal is
  never written to from two runs at once, and commits `data/` + `reports/`
  back to the repo since GitHub Actions runners don't otherwise persist
  anything between runs. `DHAN_LIVE_TRADING_ENABLED` is hardcoded to
  `"false"` directly in this file on purpose — going live means editing this
  file, a visible git diff, not flipping a secret quietly.

**Still not included, because it wasn't part of what was shared and hasn't
been built yet:** the `dashboard/` app, and the end-of-day script that
closes signals and generates the daily/weekly reports. The checklist above
is left exactly as given — nothing here has actually been marked complete,
because a real validation period hasn't run yet. Don't treat this file's
presence as evidence that Phase 9 is done.
