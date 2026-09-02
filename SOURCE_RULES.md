# SOURCE_RULES.md

This file records the supplied specification as the source of truth for source-derived rules. Source terminology is preserved; engineering/safety rules are explicitly separated.

| SOURCE | EXACT OBSERVATION | INTERPRETATION | IMPLEMENTATION | CONFIGURATION | CATEGORY |
|---|---|---|---|---|---|
| Supplied specification §21 | LAW OF VALUE MIGRATION; factors TECHNOLOGY, COST, REGULATORY, CONSUMER PREFERENCE | Analytical dimensions, not numeric triggers | Sector/theme value-migration research | `config/settings.yaml` when added | SOURCE-DERIVED RESEARCH |
| Supplied specification §25 | SECTORS > 15%; COMPANIES > 25%; RED FLAGS → REJECTION | Preserve thresholds exactly | SCRAP rejection gate | Configurable but source defaults remain 15/25 | SOURCE-DERIVED |
| Supplied specification §27 | STOCK PRICE = EPS × P/E | Research/valuation relationship | `source_valuation()` | No direct intraday trigger | SOURCE-DERIVED |
| Supplied specification §31 | ROCE = PROFIT / CAPITAL × 100 | Preserve formula | `source_roce()` | Capital definition must be verified if source is more specific | SOURCE-DERIVED |
| Supplied specification §32 | R.R = (F.A + W.C) / CASH | Handwritten/unclear abbreviation meaning | Preserve as reference only | Not an automatic intraday trigger | SOURCE-UNCLEAR |
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
| Final project risk constraint | Start with a maximum of 2 trades per day | Daily trade-count limit is deterministic and cannot be bypassed by AI/dashboard | `risk_gate()` counts filled simulated entries for the current IST date | `MAX_TRADES_PER_DAY=2` | ENGINEERING/SAFETY |
| Final project risk constraint | Maximum consecutive losses and emergency stop are deterministic risk controls | Block new trades when configured safety limits are reached | `risk_gate()` | `MAX_CONSECUTIVE_LOSSES=3`, `BOT_EMERGENCY_STOP=false` | ENGINEERING/SAFETY |
| Final project heartbeat fix | Scheduler heartbeat is not proof of a live trading worker | Dashboard must distinguish scheduler liveness from successful market-cycle liveness | Scheduler heartbeat is written every scheduled invocation; worker heartbeat records cycle success/degradation/heartbeat-only state separately | 5-minute scheduled interval, all days | ENGINEERING/SAFETY |
| Paper-validation risk decision | ₹1,000 virtual account requires meaningful absolute safety gates | Scale absolute limits instead of retaining real-capital values that cannot bind | `daily_loss_limit=₹20`, `max_position_exposure=₹800` by default | `MAX_DAILY_LOSS=20`, `MAX_POSITION_EXPOSURE=800` | ENGINEERING/SAFETY |
| Runtime ownership decision | `runtime.py` is the active engine; `runtime_v2.py` was an obsolete parallel implementation | One active runtime prevents future edits to the wrong engine | `scripts/run_daily_cycle.py` → `intraday_bot.runtime.run_cycle()`; obsolete `runtime_v2.py` removed | N/A | ENGINEERING |
| Dhan paper-data credential decision | Current paper phase uses the longer-valid Dhan credential configured as `DHAN_API_KEY`; short-lived access token is not required when the API key is present | Avoid making daily token regeneration a development blocker | `DhanBroker` uses API key first and optional access-token fallback | `DHAN_API_KEY` preferred; `DHAN_ACCESS_TOKEN` fallback only | ENGINEERING/SAFETY |
| Dhan market-data hardening | Authentication success alone does not prove market-feed access | Preflight the real market-feed path and never fabricate quotes | Workflow validates one mapped quote; `DhanBroker.bulk_quotes()` can fall back to Dhan LTP endpoint and reports both errors if neither works | `DHAN_SECURITY_IDS_JSON` for quote preflight | ENGINEERING/SAFETY |
| Dashboard truthfulness fix | Scheduler/heartbeat timestamps must not be interpreted with a server-local timezone | Compare aware timestamps in UTC | `heartbeat_age_seconds()` parses timezone-aware ISO timestamps | N/A | ENGINEERING/SAFETY |
| Dashboard refresh fix | Manual refresh must visibly report whether persisted state was actually retrieved | A button click alone is not proof of fresh data | `sync(force=True)` records sync success/failure and the UI exposes the result | N/A | ENGINEERING |

## Important separation

Long-term concepts such as Compounders, 100 Baggers, Buffett, Peter Lynch, ROCE, EPS × P/E, Predictability and Value Migration are **research/quality/conviction/context inputs**, not automatic intraday BUY triggers. Intraday execution remains dependent on current intraday conditions.

## PAPER CAPITAL RULE

The current paper-testing account is virtual. `PAPER` position sizing must use `BOT_RESEARCH_REFERENCE_CAPITAL` and must not be reduced by the real Dhan account balance. This rule does not authorize real trading.

## PAPER RISK RULE

For the current ₹1,000 validation account, the default daily loss ceiling is ₹20 and the maximum single-position exposure is ₹800. These are defaults, not permanent production limits; they may be overridden through deployment secrets when the paper account is intentionally scaled.

## RISK SAFETY RULE

`BOT_EMERGENCY_STOP=true` blocks new trades. `MAX_CONSECUTIVE_LOSSES` blocks new trades once the configured number of consecutive closed losing trades is reached. `MAX_TRADES_PER_DAY=2` blocks additional simulated entries after two filled paper/live-test entries in the current IST date. These are deterministic controls and cannot be overridden by AI or the dashboard.

## 24/7 FREE-TIER RULE

The current free paper deployment uses GitHub Actions as a five-minute scheduler/heartbeat. The workflow is scheduled every five minutes every day. During NSE cash-market hours it runs the paper market cycle; outside the market window it records a heartbeat-only state. This is not a persistent Linux worker and must never be presented as one. A paid/persistent worker is a later production requirement for live trading, not a prerequisite for current paper validation.

## SOURCE-UNCLEAR policy

If a supplied screenshot/video/formula is unreadable, preserve the observed wording, mark it `SOURCE-UNCLEAR`, and keep it configurable. Do not invent the missing meaning.
