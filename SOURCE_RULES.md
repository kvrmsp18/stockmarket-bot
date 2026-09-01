# SOURCE_RULES.md

This file records the supplied specification as the source of truth for source-derived rules. Source terminology is preserved; engineering/safety rules are explicitly separated.

| SOURCE | EXACT OBSERVATION | INTERPRETATION | IMPLEMENTATION | CONFIGURATION | CATEGORY |
|---|---|---|---|---|---|
| Supplied specification §21 | LAW OF VALUE MIGRATION; factors TECHNOLOGY, COST, REGULATORY, CONSUMER PREFERENCE | Analytical dimensions, not numeric triggers | Sector/theme value-migration research | `config/settings.yaml` when added | SOURCE-DERIVED RESEARCH |
| Supplied specification §25 | SECTORS > 15%; COMPANIES > 25%; RED FLAGS → REJECTION | Preserve thresholds exactly | SCRAP rejection gate | Configurable but source defaults remain 15/25 | SOURCE-DERIVED |
| Supplied specification §27 | STOCK PRICE = EPS × P/E | Research/valuation relationship | `source_valuation()` | No direct intraday trigger | SOURCE-DERIVED |
| Supplied specification §31 | ROCE = PROFIT / CAPITAL × 100 | Preserve formula | `source_roce()` | Capital definition must be verified if source is more specific | SOURCE-DERIVED |
| Supplied specification §32 | R.R = (F.A + W.C) / CASH | Handwritten/unclear abbreviation meaning | Preserve as reference only | Not an automatic trading trigger | SOURCE-UNCLEAR |
| Supplied specification §35 | TREND SCORE 0–10; >7 BULLISH; <4 BEARISH | Transparent trend classification | `technical.py` | Thresholds configurable, source defaults 7/4 | SOURCE-DERIVED |
| Supplied specification §41 | Buffett, Rakesh Jhunjhunwala, Peter Lynch, 100 Baggers, CANSLIM | Research/conviction frameworks | `conviction()` | Framework weights may be configured | SOURCE-DERIVED RESEARCH |
| Supplied specification §67 | EOD report with starting/ending capital, funds, deployed capital, signals, trades, wins/losses, P&L, drawdown, etc. | Required reporting | Database/reporting layer | Report schedule configurable | ENGINEERING REQUIREMENT |
| Supplied specification §78 | EVERY 5 MINUTES OR FASTER; continuous service/WebSocket/event-driven preferred | Intraday monitoring minimum | GitHub Actions 5-minute fallback + independent service architecture | Interval configurable | ENGINEERING REQUIREMENT |
| Supplied specification §91 | PAPER TRADING ON; LIVE TRADING OFF; MINIMUM R:R 1:3 | Safety defaults | Runtime and UI | Configurable except live must remain explicitly activated | SOURCE/SAFETY |
| Supplied specification §127 | Never prioritize AI opinion/profit opportunity/trade count over risk, funds, data, broker, execution and reconciliation integrity | Absolute safety principle | Risk and execution gates | Not bypassable | ENGINEERING/SAFETY |
| Final project override #5 | PAPER mode uses `BOT_RESEARCH_REFERENCE_CAPITAL`; current testing baseline ₹1,000 and must not depend on real Dhan cash | Isolate virtual paper funds from broker balance | `position_size()` uses supplied paper capital as its funds ceiling | `BOT_RESEARCH_REFERENCE_CAPITAL=1000` | ENGINEERING/SAFETY |
| Final project override #6 | LIVE remains disabled during paper validation | No real order submission during paper validation | `DHAN_LIVE_TRADING_ENABLED=false` plus mode gate | Explicit live activation required | ENGINEERING/SAFETY |
| Final project override #7 | Every 5 minutes or faster; prevent overlapping cycles | Scheduler must not create duplicate work | GitHub Actions schedule + concurrency | `CYCLE_BUDGET_SECONDS=240` | ENGINEERING |
| Final project override #13 | Raw audit must identify repository, branch, commit, generation time, inventory, sizes and source/exclusions | Current-state snapshot must be reproducible | Automated audit workflow | `.github/workflows/export-source-audit.yml` | ENGINEERING |
| Final project override #15 | Validate source audit → syntax/import → tests → Actions → paper cycle → persisted state → dashboard → candidate/rejection verification | CI passing alone is insufficient | Release/verification sequence | N/A | ENGINEERING |
| Final project override #5/#6 | Maximum consecutive losses and emergency stop are deterministic risk controls | Block new trades when configured safety limits are reached | `risk_gate()` | `MAX_CONSECUTIVE_LOSSES=3`, `BOT_EMERGENCY_STOP=false` | ENGINEERING/SAFETY |

## Important separation

Long-term concepts such as Compounders, 100 Baggers, Buffett, Peter Lynch, ROCE, EPS × P/E, Predictability and Value Migration are **research/quality/conviction/context inputs**, not automatic intraday BUY triggers. Intraday execution remains dependent on current intraday conditions.

## PAPER CAPITAL RULE

The current paper-testing account is virtual. `PAPER` position sizing must use `BOT_RESEARCH_REFERENCE_CAPITAL` and must not be reduced by the real Dhan account balance. This rule does not authorize real trading.

## RISK SAFETY RULE

`BOT_EMERGENCY_STOP=true` blocks new trades. `MAX_CONSECUTIVE_LOSSES` blocks new trades once the configured number of consecutive closed losing trades is reached. These are deterministic controls and cannot be overridden by AI or the dashboard.

## SOURCE-UNCLEAR policy

If a supplied screenshot/video/formula is unreadable, preserve the observed wording, mark it `SOURCE-UNCLEAR`, and keep it configurable. Do not invent the missing meaning.
