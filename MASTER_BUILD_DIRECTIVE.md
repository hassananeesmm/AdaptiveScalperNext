================================================================================
ADAPTIVE SCALPER NEXT
COMPLETE AUTHORITATIVE MASTER BUILD DIRECTIVE
FROM-SCRATCH WINDOWS + MT5 DEMO ADAPTIVE SCALPING SYSTEM
================================================================================

VERSION PURPOSE
--------------

This document is the SINGLE AUTHORITATIVE SPECIFICATION for the new
Adaptive Scalper project.

It supersedes older Adaptive Scalper prompts, old repaired binaries,
old recovered bytecode, old dynamic-universe requirements, and old
architecture decisions wherever they conflict with this directive.

DO NOT simplify this directive.

DO NOT omit difficult sections because they take longer.

DO NOT create decorative/showpiece subsystems that are disconnected
from the real decision path.

Build, test, connect, verify and document the actual system.

================================================================================
1. YOUR ROLE
================================================================================

You are Claude Code acting as:

- principal software architect
- senior Python engineer
- quantitative systems engineer
- MetaTrader 5 integration engineer
- database engineer
- machine-learning engineer
- local-RAG engineer
- reliability engineer
- test engineer
- Windows automation engineer
- dashboard/frontend engineer
- release engineer

You are responsible for building this project from scratch.

Do not merely tell me what code I should write.

Work directly in the workspace.

Create files.

Edit files.

Run commands.

Install appropriate dependencies.

Run tests.

Fix failures.

Run integration checks.

Build the Windows release.

Verify the release.

Use Git.

Maintain documentation.

Continue autonomously through the implementation plan.

Do NOT repeatedly stop to ask me architectural questions when a safe,
testable engineering decision can be made from this specification.

Only request user intervention when genuinely unavoidable, such as:

- MT5 login
- selecting an ambiguous installed terminal
- Windows permission requiring manual approval
- external authentication genuinely required
- information unavailable from the machine

Otherwise continue.

================================================================================
2. NEW PROJECT — DO NOT PATCH THE OLD BOT
================================================================================

Build a CLEAN NEW SOURCE REPOSITORY.

Preferred path:

C:\AdaptiveScalperNext

The existing project, if present:

C:\AdaptiveScalper

must remain untouched as a historical reference.

Do not make the new system dependent on:

- AdaptiveScalper.exe
- AdaptiveScalperSafe.exe
- recovered .pyc files
- decompiled bytecode
- recovery overlays
- old source fragments

You may inspect old artifacts only for useful historical lessons.

The new project must have maintainable original Python source.

Initialize Git immediately.

Create a first clean commit after the project skeleton is established.

================================================================================
3. CORE OBJECTIVE
================================================================================

Build a complete LOCAL adaptive scalping research, learning, PAPER and
MetaTrader 5 DEMO execution platform.

Runtime architecture:

WINDOWS COMPUTER
        ↓
ADAPTIVE SCALPER NEXT
        ↓
LOCAL SQLITE DATABASE
        ↓
LOCAL MODELS
        ↓
LOCAL RAG / EXPERIENCE MEMORY
        ↓
LOCAL REAL-TIME DASHBOARD
        ↓
METATRADER 5
        ↓
DEMO BROKER

The finished runtime must NOT require:

- Claude
- Anthropic API
- OpenAI API
- ChatGPT
- cloud LLMs
- paid AI inference
- hosted databases
- hosted dashboards
- cloud vector databases

Claude is a DEVELOPMENT TOOL only.

Closing Claude must not stop Adaptive Scalper.

================================================================================
4. ABSOLUTE DEMO-ONLY INTERLOCK
================================================================================

THIS PROJECT MUST NEVER TRADE REAL MONEY.

Supported runtime execution modes:

PAPER
DEMO

Do not implement a usable LIVE/REAL-money order mode.

Immediately before every MT5 order request:

1. obtain fresh account information
2. positively establish that the connected account is DEMO
3. verify terminal Algo Trading permission
4. verify broker/account trading permission
5. verify MT5 connection
6. verify effective gateway
7. verify no critical reconciliation state
8. verify no dangerous UNKNOWN execution
9. verify persistent kill-switch state
10. run final deterministic permission gate

If DEMO status cannot be positively proven:

BLOCK ORDER SUBMISSION.

If MT5 changes from a DEMO account to a real account while the engine is
running:

BLOCK NEW ORDER SUBMISSION IMMEDIATELY.

Never infer DEMO merely from configuration.

Broker/account truth wins.

Position reconciliation and safe monitoring may continue.

================================================================================
5. EXACT TRADING UNIVERSE
================================================================================

THIS VERSION TRADES EXACTLY THREE CANONICAL MARKETS:

XAUUSD
GBPJPY
BTCUSD

Create a hard canonical allow-set equivalent to:

ALLOWED_CANONICAL_SYMBOLS = frozenset({
    "XAUUSD",
    "GBPJPY",
    "BTCUSD",
})

No fourth canonical symbol may produce:

- an active strategy signal
- an executable proposal
- a trading order
- a new position

Examples that must be blocked:

EURUSD
USDJPY
ETHUSD
DE40
US30
XAUEUR
XAUGBP
XAUJPY
and everything else.

================================================================================
6. BROKER SYMBOL RESOLUTION
================================================================================

Do not blindly assume IC Markets or another MT5 broker uses the exact names:

XAUUSD
GBPJPY
BTCUSD

Implement a robust SymbolResolver.

For each canonical symbol:

1. try exact broker name
2. inspect broker symbols and metadata
3. identify reasonable prefix/suffix aliases
4. inspect description
5. inspect base/profit/margin currencies
6. inspect trade_calc_mode
7. inspect contract specification
8. ensure exactly one safe match
9. persist mapping

Example:

canonical     broker symbol
XAUUSD        XAUUSD
GBPJPY        GBPJPY
BTCUSD        BTCUSD

or broker-specific equivalents.

If:

- no valid match
- multiple ambiguous matches
- unusable specification
- symbol cannot trade

then fail closed for that canonical instrument.

Never substitute another asset.

Final permission gate independently checks canonical symbol allow-list.

Block reason:

BLOCK_SYMBOL_NOT_ALLOWED

================================================================================
7. THREE SYMBOL SPECIALIST PROFILES
================================================================================

Use one common engine but maintain separate learned behavior for:

XAUUSD
GBPJPY
BTCUSD

These markets must NOT automatically inherit each other's:

- spread assumptions
- volatility expectations
- execution cost
- slippage
- holding-time profile
- strategy score
- calibration
- session behavior
- exit behavior
- model evidence

Maintain symbol-specific contextual statistics with sensible hierarchical
fallbacks.

================================================================================
8. PERMANENTLY RETIRED STRATEGIES
================================================================================

These two strategy identifiers are PERMANENTLY RETIRED:

failed_breakout_fade
support_resistance_reaction

Create a hard central state equivalent to:

RETIRED_STRATEGY_KEYS = frozenset({
    "failed_breakout_fade",
    "support_resistance_reaction",
})

They must NEVER:

- register as active strategies
- generate a live signal
- generate a proposal
- enter active ranking
- place an order
- become champion
- become challenger
- train as an active model target
- be promoted
- be re-enabled by ML
- be re-enabled by RAG
- be re-enabled by configuration
- be re-enabled after restart
- be re-enabled after migration
- be re-enabled after packaging
- be re-enabled by model registry restore

Historical records mentioning them may remain.

Historical RAG memories mentioning them may remain.

Historical broker/database evidence must not be deleted.

But those records must NOT make the strategy active.

Final permission gate must contain independent defense:

if strategy_key in RETIRED_STRATEGY_KEYS:
    BLOCK_STRATEGY_RETIRED

================================================================================
9. ACTIVE STRATEGY FAMILIES
================================================================================

Initial active strategy families:

1. momentum_continuation
2. pullback_continuation
3. range_breakout
4. statistical_reversion
5. volatility_expansion
6. microstructure_acceleration

Create one Strategy interface.

A strategy may produce:

- strategy key
- strategy version
- symbol
- direction
- raw confidence
- stop distance
- target distance
- expected duration
- entry method
- feature snapshot
- regime
- rationale
- hypothesis metadata

A strategy must NOT decide:

- money risk
- volume
- account safety
- news permission
- portfolio risk
- kill switch
- final execution permission

================================================================================
10. FLAT IS A VALID POSITION STATE
================================================================================

The decision system must explicitly understand:

LONG
SHORT
FLAT

FLAT is a valid deliberate decision.

The objective is NOT to constantly choose BUY or SELL.

The engine must be capable of concluding:

NO TRADE
REMAIN FLAT

without treating this as system failure.

================================================================================
11. FEATURE ENGINE
================================================================================

Build a reusable broker-independent FeatureEngine.

Possible causal inputs:

- OHLC
- tick data
- bid
- ask
- spread
- spread change
- tick frequency
- tick volume where meaningful
- returns
- log returns
- realized volatility
- ATR-like normalized range
- velocity
- acceleration
- range
- range expansion
- compression
- candle body/wicks
- recent highs
- recent lows
- swing structure
- trend strength
- directional persistence
- efficiency ratio
- momentum
- mean deviation
- abnormal movement
- volatility percentile
- spread percentile
- movement-to-cost
- session state
- time-of-day
- cross-symbol correlation

NO LOOKAHEAD.

Persist:

feature_schema_version
feature_timestamp
data_timestamp
resolution set

================================================================================
12. MULTI-RESOLUTION ANALYSIS
================================================================================

This is a short-duration scalping system.

Possible initial MT5 information resolutions:

M1
M2
M3
M5
M15

Do not declare one resolution universally correct.

Short resolutions may provide execution detail.

Broader short-term resolutions may provide context.

Persist the resolution set used for every decision.

Measure performance by resolution combination.

================================================================================
13. MARKET REGIME
================================================================================

Build deterministic regime classification.

Possible states:

TRENDING_UP
TRENDING_DOWN
RANGE
COMPRESSION
VOLATILITY_EXPANSION
BREAKOUT
ERRATIC
UNKNOWN

Persist:

entry regime
current regime
regime confidence
regime version

Do not close a position solely because one noisy observation briefly flips
regime.

Use persistence where appropriate.

================================================================================
14. SYSTEM OPERATING LOOP
================================================================================

Required high-level loop:

ACCOUNT HEALTH
↓
RECONCILIATION
↓
UNKNOWN EXECUTION RESOLUTION
↓
EXISTING POSITIONS
↓
CONTINUOUS POSITION EXPECTANCY
↓
PROTECT / EXIT IF REQUIRED
↓
GLOBAL NEW-ENTRY PERMISSION
↓
NEWS
↓
THREE SYMBOL MARKET DATA
↓
FEATURES
↓
REGIME
↓
ACTIVE STRATEGIES
↓
JOURNAL
↓
RAG RETRIEVAL
↓
MODEL / SELF-LEARNING CONTEXT
↓
SELECTOR
↓
COST
↓
EXPECTED NET EDGE
↓
CORRELATION
↓
PORTFOLIO HEAT
↓
RISK GOVERNOR
↓
BROKER VALIDATION
↓
FINAL NEWS CHECK
↓
FINAL DEMO ACCOUNT CHECK
↓
FINAL TRADE PERMISSION
↓
MT5 DEMO REQUEST
↓
BROKER RESULT
↓
RECONCILIATION
↓
POSITION MANAGEMENT
↓
FINAL RESULT
↓
JOURNAL OUTCOME
↓
LEARNING
↓
RAG INGESTION
↓
REPEAT

Every required component must genuinely participate.

================================================================================
15. EXISTING POSITIONS ALWAYS HAVE PRIORITY
================================================================================

Open risk has higher priority than searching for new trades.

Every cycle prioritizes:

1. MT5/account health
2. reconciliation
3. UNKNOWN execution resolution
4. open positions
5. protective orders
6. continuous position expectancy
7. adaptive exit
8. only then new opportunities

Never delay management of a real DEMO position because feature scanning is busy.

================================================================================
16. SEPARATE EXECUTION CADENCES
================================================================================

Use separate configurable schedulers.

Initial values to benchmark:

position review:
approximately 0.5–1.0 seconds

new-entry analysis:
approximately 4 seconds

dashboard state push:
approximately 1 second

news remote refresh:
approximately 15–30 minutes

model training:
asynchronous / much slower

historical synchronization:
background / controlled

RAG ingestion:
asynchronous/batched where appropriate

Measure performance.

Avoid concurrent unsafe MT5 calls.

Use appropriate gateway serialization/locking.

================================================================================
17. DO NOT ONLY WAIT FOR SL OR TP
================================================================================

Broker SL and TP remain mandatory hard protection.

But the application must continuously calculate whether holding the current
trade still has positive expected value.

Every position review should ask:

"If this position did not already exist right now, using everything currently
known, would opening approximately this same exposure still be justified
after costs?"

This is:

CURRENT POSITION EXPECTANCY.

Possible actions:

HOLD
MOVE_PROTECTIVE_STOP
FULL_CLOSE

Partial close can remain disabled in initial production DEMO release.

================================================================================
18. ENTRY, EXIT AND RE-ENTRY ARE DIFFERENT DECISIONS
================================================================================

Do not use one identical score for everything.

ENTRY MODEL / LOGIC asks:

"Is opening exposure worthwhile now?"

EXIT MODEL / LOGIC asks:

"Given that exposure already exists, is its remaining expected value worth
continuing to hold?"

RE-ENTRY LOGIC asks:

"After paying another spread/commission/slippage cycle, has a sufficiently
strong NEW opportunity appeared?"

Keep these concepts distinct.

================================================================================
19. POSITION EXPECTANCY
================================================================================

Evaluate current holding state using:

- original strategy hypothesis
- current hypothesis validity
- current regime
- entry regime
- momentum
- trend
- velocity
- acceleration
- volatility
- spread
- spread percentile
- movement-to-cost
- liquidity
- model probability
- RAG evidence
- news proximity
- correlation
- portfolio exposure
- current R
- peak R
- giveback
- holding duration
- remaining expected reward
- expected remaining cost

Allow early exit when deterministic rules show:

- original thesis invalidated
- setup materially deteriorated
- regime materially reversed
- expected remaining net edge became negative
- accumulated profit materially retraced
- early profit objective achieved
- maximum useful holding time expired
- liquidity/cost became hostile
- emergency condition exists

Do not close because of tiny market noise.

================================================================================
20. INITIAL ADAPTIVE EXIT PARAMETERS
================================================================================

Initial configurable research parameters:

adaptive_profit_exit_enabled = true

min_profit_r = 0.30

profit_protection_trigger_r = 0.60

max_profit_retracement_r = 0.20

early_take_profit_r = 1.00

breakeven_enabled = true

breakeven_trigger_r = 0.40

breakeven_floor_r = 0.05

setup_deterioration_exit = true

regime_reversal_exit = true

max_holding_enabled = true

max_holding_seconds = 600

partial_close_enabled = false

These are STARTING DEMO settings.

Do not describe them as optimal or profitable.

================================================================================
21. FIXED INITIAL MONETARY RISK
================================================================================

Initial monetary risk is frozen at entry.

It does not change because:

- SL moves
- price moves
- trade becomes profitable

current_r =
    current unrealized net profit / initial monetary risk

peak_r =
    maximum valid current_r observed during the position

Persist:

initial risk
current R
peak R
peak timestamp

across restart.

================================================================================
22. PROFIT PROTECTION EXAMPLE
================================================================================

Example:

peak R = +0.85
current R = +0.65
giveback = +0.20

If configured deterministic conditions are satisfied:

FULL_CLOSE

may be preferable to waiting until TP or allowing the trade to reverse toward
SL.

This does NOT mean every profitable trade should be immediately closed.

Allow valid trades to develop.

================================================================================
23. BREAKEVEN / PROTECTIVE STOP
================================================================================

Protective SL may advance when evidence justifies it.

Never move a protective stop backward.

Respect:

- bid/ask
- digits
- tick size
- minimum stop distance
- freeze level
- broker rules

Example starting research concept:

trigger:
+0.40R

protected floor:
+0.05R

================================================================================
24. EXIT LATENCY MEASUREMENT
================================================================================

For application-driven exit record:

threshold_cross_time
decision_time
request_time
broker_response_time
fill_time

peak_r
decision_r
fill_r

giveback_at_decision
giveback_at_fill

estimated slippage
realized slippage

Determine whether excessive giveback was caused by:

- review cadence
- quote movement
- MT5 latency
- broker execution
- database delay
- implementation bug

================================================================================
25. APPLICATION CLOSE SAFETY
================================================================================

Implement close_position thoroughly.

Test:

- full close
- broker reject
- requote
- slippage
- timeout
- unknown outcome
- restart during close
- position already closed by SL
- position already closed by TP

Critical race:

application decides CLOSE

but broker SL/TP closes position milliseconds earlier.

The application must reconcile broker truth.

It must never accidentally create an opposite position.

================================================================================
26. SAFE RE-ENTRY
================================================================================

An application-driven exit does NOT automatically create a re-entry.

After an exit, the previous trade is FINISHED.

Any re-entry must be a NEW trade.

It must pass again through the entire pipeline:

new data
↓
new features
↓
new regime
↓
strategy
↓
RAG
↓
model
↓
news
↓
cost
↓
expected edge
↓
correlation
↓
portfolio
↓
risk
↓
broker validation
↓
permission
↓
DEMO execution

Never implement:

CLOSE
→ AUTOMATIC REOPEN

================================================================================
27. RE-ENTRY MUST REQUIRE STRONGER EVIDENCE
================================================================================

Use configurable re-entry hysteresis.

Initial research concept:

normal entry eligibility threshold:
example 0.62

same-direction re-entry:
example 0.70

minimum improvement:
example +0.08

These exact numbers must remain configurable and eventually be validated.

The permanent principle is:

RE-ENTRY REQUIRES STRONGER OR GENUINELY NEW EVIDENCE.

Possible requirements:

- new setup fingerprint
- meaningful feature reset
- materially stronger expected edge
- regime transition
- configured cooldown elapsed

================================================================================
28. ANTI-CHURN
================================================================================

Prevent:

OPEN
CLOSE
OPEN
CLOSE
OPEN

caused by tiny fluctuations.

Track:

last entry time
last exit time
last direction
last strategy
last setup fingerprint
last exit reason
cost paid
cooldown

Create block reason such as:

BLOCK_REENTRY_CHURN

A re-entry must comfortably overcome another:

spread
commission
slippage
uncertainty margin

================================================================================
29. ORDER STATE MACHINE
================================================================================

Use explicit execution states:

PROPOSED
SUBMITTED
ACCEPTED
PENDING
RESTING
PARTIAL
FILLED
REJECTED
CANCELLED
EXPIRED
UNKNOWN
PENDING_RECONCILIATION

Broker acknowledgement is not automatically a fill.

Persist:

client request ID
broker order ID
deal IDs
position ID

Use idempotency.

================================================================================
30. UNKNOWN EXECUTION
================================================================================

If an order or close outcome is uncertain:

DO NOT BLINDLY RESEND.

Mark:

UNKNOWN
or
PENDING_RECONCILIATION

Resolve using broker:

open positions
current orders
order history
deal history
IDs

A dangerous unresolved UNKNOWN may block new entries.

Position protection continues.

================================================================================
31. RECONCILIATION
================================================================================

Broker is authoritative for:

- orders
- fills
- deals
- positions
- close state
- exit VWAP
- commission
- swap
- realized broker P/L

Reconcile:

startup
reconnect
after execution
after close
periodically

Handle:

orphan broker position
missing local position
manual MT5 modification
SL close
TP close
partial close
UNKNOWN resolution
restart

Never substitute current market price for a historical exit.

================================================================================
32. RISK GOVERNOR
================================================================================

RiskGovernor is the ONLY component that determines monetary position size.

Initial conservative DEMO configuration:

risk_per_trade_pct = 0.25

max_total_open_risk_pct = 0.75

max_daily_loss_pct = 2.00

max_drawdown_pct = 5.00

max_open_positions = 2

max_positions_per_symbol = 1

These are safety starting points, not profitability claims.

Learning may NEVER raise these automatically.

================================================================================
33. SAFE VOLUME
================================================================================

Use actual broker:

tick size
tick value
contract size
entry price
stop distance
min volume
max volume
volume step

Round volume DOWN.

If safe calculated position is below broker minimum:

REJECT.

Never raise to broker minimum if doing so exceeds safe monetary risk.

No martingale.

No revenge trading.

No doubling losses.

No grid-loss recovery.

No uncontrolled averaging down.

================================================================================
34. EXPECTED TRANSACTION COST
================================================================================

Scalping is highly cost sensitive.

Estimate individually for each symbol:

spread
commission
slippage
swap where relevant

Do not use one generic forex cost for all markets.

Expected calculation concept:

expected_net_edge =
    expected_gross_edge
    - expected_spread
    - expected_commission
    - expected_slippage
    - expected_swap_if_relevant
    - uncertainty_margin

Require sufficiently positive net edge.

Track:

estimated cost
realized cost
prediction error

================================================================================
35. CORRELATION AND PORTFOLIO HEAT
================================================================================

Even with three symbols, model portfolio exposure.

Calculate valid rolling correlations among:

XAUUSD
GBPJPY
BTCUSD

Use aligned observations.

Require sufficient sample size.

Missing/invalid correlation = N/A, not zero.

Track:

total open monetary risk
symbol exposure
currency-direction exposure
USD-related exposure
correlated cluster exposure
pending-order exposure

Learning may estimate relationships.

Learning may NOT override hard portfolio limits.

================================================================================
36. FINAL TRADE PERMISSION GATE
================================================================================

Immediately before every new broker execution, produce exactly one explicit
permission result.

Examples:

ALLOW

BLOCK_MODE
BLOCK_ACCOUNT_NOT_DEMO
BLOCK_MT5_DISCONNECTED
BLOCK_TERMINAL_TRADING_DISABLED
BLOCK_BROKER_TRADING_DISABLED
BLOCK_KILL_SWITCH
BLOCK_RECONCILIATION
BLOCK_UNKNOWN_ORDER
BLOCK_SYMBOL_NOT_ALLOWED
BLOCK_STRATEGY_RETIRED
BLOCK_SESSION
BLOCK_DATA_QUALITY
BLOCK_STALE_QUOTE
BLOCK_NEWS
BLOCK_NEWS_CALENDAR_UNAVAILABLE
BLOCK_NEWS_PROVIDER_CONFLICT
BLOCK_COST
BLOCK_EXPECTED_EDGE
BLOCK_CORRELATION
BLOCK_PORTFOLIO_RISK
BLOCK_RISK
BLOCK_MARGIN
BLOCK_BROKER_CONSTRAINT
BLOCK_DUPLICATE
BLOCK_REENTRY_CHURN
BLOCK_OTHER

Persist every permission result.

No model, RAG or strategy may bypass it.

================================================================================
37. PERSISTENT KILL SWITCH
================================================================================

Kill switch state must survive restart.

It never automatically clears.

ENGAGED means:

NO NEW EXPOSURE.

Safe existing-position management and reconciliation continue where
appropriate.

Clear requires explicit operator action with reason.

ML/RAG cannot change kill switch.

================================================================================
38. KEYLESS ECONOMIC NEWS SYSTEM
================================================================================

News protection is mandatory.

Do NOT require a paid Trading Economics key.

Implement:

EconomicCalendarProvider abstraction.

PRIMARY:

FinanceCalendar official API.

At implementation time, verify its current official documentation and terms.

It currently provides structured calendar data without requiring an API key.

Respect required attribution.

SECONDARY:

Forex Factory structured weekly calendar export.

Prefer structured JSON.

Do NOT scrape arbitrary HTML.

TERTIARY:

last-known-good local cache.

OPTIONAL MANUAL FALLBACK:

operator-supplied normalized JSON.

Never interpret provider failure as:

"no events."

================================================================================
39. NEWS NORMALIZED EVENT MODEL
================================================================================

Create EconomicEvent fields such as:

event_id
provider
provider_event_id
scheduled_at_utc
country
currency
title
normalized_event_type
impact
actual
forecast
previous
retrieved_at_utc
source_reliability
revision
source_identifier

Deduplicate equivalent events across providers.

Normalize aliases.

================================================================================
40. IMPORTANT NEWS EVENTS
================================================================================

Recognize at minimum:

FOMC rate decision
Fed policy statement
Fed press conference
US CPI
Core CPI
US NFP
major employment releases
PCE
Core PCE
BoE rate decision
important GBP inflation/employment
BoJ rate decision
important JPY inflation/employment
other HIGH/RED USD/GBP/JPY events

Systemic global events:

FOMC
US CPI
US Core CPI
US NFP

block NEW exposure across all three symbols.

Relevant currency logic:

XAUUSD:
USD high-impact

BTCUSD:
USD high-impact

GBPJPY:
GBP high-impact
JPY high-impact

================================================================================
41. NEWS WINDOW
================================================================================

Default:

15 minutes BEFORE HIGH event

30 minutes AFTER HIGH event

Example event:

16:30

news clear:
16:14:59

blocked:
16:15:00 through 16:59:59

news window clear:
17:00:00

News clearing does NOT force a trade.

Normal pipeline still applies.

================================================================================
42. NEWS DOES NOT STOP POSITION MANAGEMENT
================================================================================

During news continue:

reconciliation
position monitoring
SL
TP
profit protection
adaptive exit
application close
emergency handling

News controls NEW exposure.

================================================================================
43. CALENDAR FAILURE
================================================================================

If calendar is:

UNAVAILABLE
STALE beyond safe limit

then:

NEW ENTRIES BLOCKED

reason:

NEWS_CALENDAR_UNAVAILABLE

Do NOT automatically engage persistent master kill switch solely because
news service is unavailable.

================================================================================
44. NEWS PROVIDER HEALTH
================================================================================

States:

HEALTHY
DEGRADED
STALE
UNAVAILABLE
CONFLICT

At startup:

load cache
refresh provider
normalize
validate
deduplicate
persist cache

Refresh on slower cadence, approximately 15–30 minutes, configurable.

Do not perform remote HTTP request every entry cycle.

Perform a local FINAL news check immediately before order send.

If trusted providers materially disagree on timing of a HIGH-impact event:

use conservative block

NEWS_PROVIDER_CONFLICT.

================================================================================
45. GLOBAL ENTRY SHORT-CIRCUIT
================================================================================

When global new entries are blocked due to:

kill switch
unsafe account
news outage
systemic news
dangerous UNKNOWN
critical reconciliation
critical DB condition

do NOT keep running expensive strategy calculations and generating identical
proposals every four seconds.

Continue:

position management
reconciliation
health
news refresh

Record block-state transitions.

Rate-limit repetitive logs.

================================================================================
46. FIVE-YEAR MT5 HISTORICAL BOOTSTRAP
================================================================================

Before serious learning begins, obtain historical market data for:

XAUUSD
GBPJPY
BTCUSD

Target:

up to FIVE YEARS before current date.

Do NOT claim five years were obtained unless the broker actually supplied it.

For each symbol/resolution:

- request data
- determine actual earliest timestamp
- determine latest timestamp
- validate sorting
- validate OHLC
- detect duplicates
- identify gaps
- preserve legitimate session gaps
- record provenance
- persist import metadata
- record checksum where practical

================================================================================
47. HISTORICAL BAR RESOLUTIONS
================================================================================

Target at least:

M1
M2/M3 if practically supported and useful
M5
M15

Potential broader research resolution may be included when justified.

Do not create huge duplicated datasets unnecessarily.

================================================================================
48. MT5 TICK HISTORY
================================================================================

Attempt to obtain tick history for:

XAUUSD
GBPJPY
BTCUSD

up to broker-supported historical availability.

Do NOT assume five years of tick data is practical or available.

Download in chunks.

Measure:

storage size
download duration
coverage
data quality

Prefer:

up to five years bars

plus:

maximum practical/broker-available tick history.

If five years of raw ticks would create unreasonable storage/processing
requirements:

retain a shorter raw tick horizon and older derived aggregate summaries.

Document exact decision.

================================================================================
49. HISTORICAL DOWNLOAD MUST BE RESUMABLE
================================================================================

Never request five years in one unsafe in-memory operation.

Use chunked import.

Persist bootstrap checkpoints.

If process stops:

resume from last verified chunk.

Do not redownload complete five-year history every startup.

After initial bootstrap:

incrementally synchronize only missing/new data.

================================================================================
50. HISTORICAL DATA COVERAGE
================================================================================

Store and display per symbol:

requested start
actual earliest bar
actual latest bar
bar count
resolution
gaps
tick earliest
tick latest
tick count
last synchronization
health

Never say:

"5 years loaded"

unless true.

================================================================================
51. IMPORT CONNECTED MT5 ACCOUNT HISTORY
================================================================================

Import maximum broker-exposed account history for the currently connected
DEMO account.

Import:

historical orders
historical deals

Capture where available:

broker
server
account scope
order ticket
deal ticket
position ID
symbol
direction
volume
type
entry time
entry price
exit time
exit price
SL
TP
commission
swap
profit
comment
magic number
broker reason fields

Deduplicate by authoritative broker identifiers.

Import must be IDEMPOTENT.

Repeated import creates no duplicates.

================================================================================
52. EXTERNAL/MANUAL HISTORY
================================================================================

Do not pretend imported account trades were generated by Adaptive Scalper.

Unless real provenance proves otherwise, label:

origin = BROKER_ACCOUNT_HISTORY

strategy = MANUAL
or
EXTERNAL
or
UNKNOWN

Never infer originating strategy from outcome.

Unknown external trades may help with:

commission statistics
swap
execution behavior
broker characteristics
slippage research where valid

but should not automatically alter strategy performance statistics.

================================================================================
53. SQLITE AUTHORITATIVE LOCAL STORE
================================================================================

Use SQLite with:

WAL
foreign keys
migrations
integrity checks
indexes
transaction boundaries

Possible tables:

schema_migrations
app_state
configuration_audit

symbol_mapping
historical_import_jobs
historical_bar_coverage
historical_tick_coverage

signals
proposals
permission_decisions

orders
deals
positions
position_management_state
trade_results

journal_events
decision_chains
decision_quality_reviews

strategy_registry
strategy_scores

datasets
dataset_usage
model_registry
model_runs
model_predictions

news_events
news_provider_state

cost_observations
correlation_snapshots
risk_snapshots

rag_memories
rag_index_metadata

learning_events
drift_events
health_events

Use coherent schema rather than duplicated state.

================================================================================
54. IMMUTABLE STRUCTURED DECISION JOURNAL
================================================================================

Implement a REAL JOURNAL.

It is not a visual gimmick.

Use append-oriented events.

Meaningful event types include:

SIGNAL_CREATED
SIGNAL_REJECTED

PROPOSAL_CREATED
PROPOSAL_REJECTED

ENTRY_ALLOWED
ENTRY_BLOCKED

ORDER_SUBMITTED
ORDER_FILLED
ORDER_REJECTED
ORDER_UNKNOWN

POSITION_OPENED
POSITION_REVIEWED
STOP_ADVANCED
POSITION_CLOSED

REENTRY_CONSIDERED
REENTRY_ALLOWED
REENTRY_REJECTED

NEWS_BLOCK_ENTERED
NEWS_BLOCK_CLEARED

MODEL_USED
RAG_USED

RECONCILIATION_ACTION

LEARNING_UPDATE

Do not rewrite what the bot knew at an earlier timestamp.

Append subsequent outcomes.

================================================================================
55. JOURNAL DECISION DATA
================================================================================

Where relevant record:

decision timestamp
symbol
broker symbol
strategy
strategy version
direction

raw confidence
feature snapshot
regime
resolutions

model ID
model version
model score/probability

strategy evidence score

RAG query ID
RAG memories referenced
bounded RAG contribution

news state

spread
estimated commission
estimated slippage

expected gross edge
expected cost
expected net edge

correlation state
portfolio heat

risk budget
calculated safe volume

final permission
decision reason

================================================================================
56. COMPLETE CAUSAL DECISION CHAIN
================================================================================

Link:

signal
↓
proposal
↓
permission
↓
order
↓
deal
↓
position
↓
position reviews
↓
exit decision
↓
close
↓
trade result
↓
post-trade review
↓
learning
↓
RAG memory

Every outcome should be traceable backward to what was known at decision time.

================================================================================
57. JOURNAL MUST FEED LEARNING
================================================================================

Learning should consume valid journal-derived outcomes to investigate:

which setups work in which regimes
which strategies fail with excessive spread
which confidence levels are calibrated
which holding times work
which early exits protected profit
which exits cut winners too early
which re-entries helped
which re-entries created churn
which sessions differ
which news proximity is dangerous
which cost environments destroy edge

Rejected proposal != losing trade.

Keep rejection learning separate.

================================================================================
58. JOURNAL MUST FEED RAG
================================================================================

Convert appropriate finalized journal chains into typed RAG memories.

Memory types:

TRADE_SETUP
TRADE_RESULT
REJECTION
EXIT_DECISION
REENTRY_DECISION
EXECUTION_INCIDENT
STRATEGY_CONTEXT
SYSTEM_EVENT

Memory must reference authoritative source row IDs.

RAG index is derived and rebuildable.

================================================================================
59. R-MULTIPLE INTEGRITY
================================================================================

Initial monetary risk must be positive and valid.

For finalized trade:

R =
    realized net result / initial monetary risk

Never use near-zero denominator.

Handle partial closes correctly if eventually supported.

Mark corrupted/unprovable R:

QUARANTINED

Do not delete source data.

Do not allow invalid R into strategy learning.

================================================================================
60. SELF-LEARNING — NOT SELF-MODIFYING CODE
================================================================================

Self-learning does NOT mean rewriting Python source.

Never allow runtime:

eval
exec
arbitrary code injection
automatic source editing

Learning may change only validated adaptive artifacts such as:

strategy evidence scores
calibration
context weights
confidence adjustments
model artifacts
model registry state
bounded selector influence
recency weights

Learning MUST NOT change:

allowed symbols
retired strategy list
risk limits
daily loss limit
drawdown limit
kill switch
news blackout
DEMO gate
executor behavior
broker constraints
reconciliation
source code

================================================================================
61. STAGED LEARNING ACTIVATION
================================================================================

Do NOT let a fresh model immediately control trading.

Use stages:

STAGE 0
NO MODEL DATA

Deterministic strategies only.

STAGE 1
MODEL OBSERVER

Model trains and predicts but has zero execution influence.

STAGE 2
BOUNDED ADVISORY

After sufficient validated evidence, model may make small bounded selector
adjustments.

STAGE 3
VALIDATED CURRENT MODEL

Only after formal challenger validation.

At every stage:

risk/news/execution gates remain authoritative.

================================================================================
62. MINIMUM EVIDENCE
================================================================================

Do not allow ten lucky trades to dominate the system.

Every adaptive statistic must expose:

sample count
effective sample size
uncertainty
fallback level

Use conservative priors.

Do not hard-code an unrealistically tiny "learning complete" sample.

If evidence is insufficient:

INSUFFICIENT_DATA

is the correct state.

================================================================================
63. HIERARCHICAL LEARNING
================================================================================

Context fallback example:

symbol + strategy + regime + session
↓
symbol + strategy + regime
↓
symbol + strategy
↓
strategy + regime
↓
strategy global
↓
neutral prior

Small subgroups should not create massive weights.

================================================================================
64. LOCAL MACHINE LEARNING
================================================================================

Use CPU-friendly auditable local models.

Likely candidates:

LogisticRegression
HistGradientBoostingClassifier

and similarly justified sklearn models.

Do not add deep learning merely because it sounds advanced.

Models may estimate:

probability of favorable outcome before invalidation
probability of positive net outcome after costs
expected context-conditioned success
exit continuation probability

Entry and exit models may be distinct.

================================================================================
65. MODEL DATASET INTEGRITY
================================================================================

Every dataset snapshot needs:

dataset ID
created_at
time range
symbols
strategies
origin
account scope
feature version
label version
row count
excluded rows
training range
validation range
OOS range
checksum

Track dataset use.

An OOS dataset that influenced design is no longer untouched.

================================================================================
66. MODEL LIFECYCLE
================================================================================

Lifecycle:

BASELINE
↓
CHALLENGER
↓
TRAIN
↓
PURGED TEMPORAL VALIDATION
↓
WALK-FORWARD
↓
UNTOUCHED OOS
↓
REALISTIC COST TEST
↓
STABILITY TEST
↓
CALIBRATION TEST
↓
PROMOTION GATE

PASS:
CURRENT

previous CURRENT:
PREVIOUS_STABLE

FAIL:
REJECTED

degradation:
ROLLBACK

Possible states:

BASELINE
CHALLENGER
CURRENT
PREVIOUS_STABLE
REJECTED
DEGRADED
ROLLED_BACK
INSUFFICIENT_DATA

================================================================================
67. MODEL PROMOTION
================================================================================

Do not promote because:

training AUC is good
training profit is high
last ten trades won
RAG retrieved positive memories

Promotion requires robust evidence.

Metrics may include:

Brier score
log loss
calibration error
AUC where meaningful

plus:

net R
net P/L
profit factor
drawdown
stability
cost burden
sample size

================================================================================
68. CONCEPT DRIFT
================================================================================

Track drift in:

volatility
spread
movement-to-cost
trend strength
session activity
features
model confidence
calibration
strategy outcomes
holding durations
slippage

If drift occurs:

NEVER increase risk.

Instead:

reduce model influence
fallback toward deterministic baseline
mark DEGRADED
allow retraining research
retain safe incumbent until challenger passes

================================================================================
69. LOCAL RAG
================================================================================

Build a local experience-memory system.

No cloud API.

Possible initial implementation:

HashingVectorizer
or
TF-IDF
+
cosine similarity / nearest neighbors

SQLite is authoritative.

Vector/retrieval index is disposable.

Do not embed enormous raw price histories.

Embed compact factual memory summaries and structured metadata.

================================================================================
70. RAG AUTHORITY FIREWALL
================================================================================

RAG answers:

"What happened in comparable historical circumstances?"

RAG does NOT answer:

"Send this order."

RAG must not have direct trading authority.

It cannot:

execute
raise risk
change kill switch
change allowed symbols
reactivate retired strategies
override news
override cost
override portfolio
override final permission

If RAG fails:

system becomes DEGRADED

but deterministic safety remains operational.

================================================================================
71. BOUNDED RAG + ML INFLUENCE
================================================================================

Do not allow ML/RAG to transform a clearly poor setup into an executable one.

Concept:

STRATEGY HYPOTHESIS
↓
BOUNDED MODEL ADJUSTMENT
↓
BOUNDED RAG ADJUSTMENT
↓
SELECTOR
↓
HARD COST / NEWS / RISK / PERMISSION GATES

Set explicit configurable maximum influence bounds.

================================================================================
72. DECISION EXPLAINABILITY
================================================================================

Record and display:

strategy raw confidence
strategy evidence score
model probability
model calibration status
model sample count
RAG similar cases
compatible broker-DEMO cases
bounded RAG contribution
gross edge
cost
net edge
news result
correlation result
risk result
permission result

Never use:

"AI chose BUY"

as the explanation.

================================================================================
73. SESSION INTELLIGENCE
================================================================================

Measure behavior by session rather than assume universal behavior.

Track relevant time/session context for:

XAUUSD
GBPJPY
BTCUSD

Do not hard-code claims such as:

"London session is always better."

Allow evidence to determine:

cost
volatility
strategy performance
holding behavior
execution quality

by session.

================================================================================
74. DAILY SYMBOL TRADABILITY STATE
================================================================================

Give every canonical symbol a current state such as:

OPTIMAL
NORMAL
EXPENSIVE
ERRATIC
NEWS_BLOCKED
DATA_DEGRADED
UNAVAILABLE

This is descriptive system state, not permission by itself.

Final permission remains deterministic.

================================================================================
75. SYSTEM HEALTH TRADING BLOCKS
================================================================================

Do not only stop new trading based on financial loss.

New entries must also be blockable for technical health problems such as:

excessive UNKNOWN outcomes
reconciliation errors
abnormal slippage burst
news calendar unavailable
database integrity failure
broker reconnect storm
persistent MT5 errors
execution mismatch

A profitable but technically unhealthy system must not continue blindly.

================================================================================
76. DECISION QUALITY SCORE
================================================================================

After completed trading chains, create an analytical:

DECISION QUALITY REVIEW.

Do NOT reduce this to:

WIN = GOOD
LOSS = BAD

Evaluate dimensions such as:

ENTRY QUALITY
EXIT QUALITY
EXECUTION QUALITY
COST QUALITY
RISK COMPLIANCE
DATA QUALITY
JOURNAL COMPLETENESS
PROCESS COMPLIANCE

A correctly justified loss can still represent a sound decision process.

A lucky winning trade that violated safeguards is not a good decision.

Do not let this subjective review directly override hard trading gates.

Use it for research and diagnostics.

================================================================================
77. EXIT REGRET RESEARCH
================================================================================

After an application-driven exit, OFFLINE research may evaluate:

actual exit R

maximum favorable excursion after exit

maximum adverse excursion after exit

whether original TP would later have been reached

whether original SL would later have been reached

Example:

actual exit:
+0.55R

later max favorable:
+1.10R

later adverse:
-0.20R

This can help evaluate whether exits are too aggressive.

Never rewrite actual trade result.

================================================================================
78. RE-ENTRY REGRET / CHURN RESEARCH
================================================================================

Measure:

number of re-entries
same-direction re-entries
time between exit and re-entry
cost paid
re-entry net result
re-entry R
profitable re-entry rate
churn cost
re-entry avoided by cooldown
counterfactual result without re-entry

Determine whether re-entry adds edge AFTER additional cost.

================================================================================
79. COUNTERFACTUAL RESEARCH
================================================================================

Offline only, research questions such as:

What if original TP/SL had been retained?

What if profit retracement threshold were different?

What if no re-entry happened?

What if re-entry happened later?

Store separately:

counterfactual experiment ID
assumptions
result

Never replace broker-confirmed actual result.

================================================================================
80. BACKTESTING
================================================================================

Implement causal:

backtesting
walk-forward
out-of-sample evaluation
Monte Carlo/bootstrap robustness research

Only for:

XAUUSD
GBPJPY
BTCUSD

Use realistic:

spread
commission
slippage
broker contract data

Clearly label:

BACKTEST
SIMULATION
PAPER
BROKER_DEMO_CONFIRMED

Never mix them silently.

================================================================================
81. HISTORICAL NEWS LIMITATION
================================================================================

If point-in-time historical calendar data exists, apply news windows in
historical research.

If not:

explicitly state the limitation.

Do not pretend the backtest knew future historical news schedules if it did
not have point-in-time calendar information.

================================================================================
82. EVIDENCE PROVENANCE
================================================================================

Use explicit provenance:

BROKER_DEMO_CONFIRMED
PAPER_LIVE_DATA
BROKER_ACCOUNT_HISTORY
HISTORICAL_MT5_REPLAY
BACKTEST
SIMULATED
IMPORTED
UNVERIFIED

Preserve:

broker
server
account
gateway
mode
strategy version
model version
feature version

Evidence classes must not silently receive identical weight.

================================================================================
83. LIVE DASHBOARD
================================================================================

Build a polished REAL-TIME LOCAL dashboard.

Preferred architecture:

FastAPI
+
local WebSocket
+
HTML/CSS/JavaScript
+
local bundled assets

Do not require cloud hosting.

Bind by default to:

127.0.0.1

Dashboard should refresh approximately every second without blocking trading.

If WebSocket fails:

use safe polling fallback.

Display:

engine heartbeat
last update
dashboard-data freshness

No fake demo values in actual dashboard.

================================================================================
84. DASHBOARD SYSTEM PANEL
================================================================================

Show:

engine:
RUNNING / STOPPED / DEGRADED

mode:
PAPER / DEMO

configured gateway
effective gateway

MT5 connection

broker
server

account type

DEMO VERIFIED

terminal Algo Trading permission

broker trading permission

persistent kill switch

reconciliation state

UNKNOWN count

database health

news health

RAG health

learning health

model state

last engine heartbeat

================================================================================
85. DASHBOARD THREE-SYMBOL PANEL
================================================================================

One card each for:

XAUUSD
GBPJPY
BTCUSD

Show:

canonical symbol
broker symbol
bid
ask
spread
spread percentile
last tick age
trade/session state
volatility
movement-to-cost
current regime
selected resolutions
tradability state
latest strategy
latest signal
latest decision
news restriction
data quality
open-position status

================================================================================
86. DASHBOARD NEWS PANEL
================================================================================

Show:

primary provider
secondary provider
provider health
last success
cache age
next high-impact event
countdown
currency
impact
title
affected symbols
block start
block end

Current state examples:

NEW ENTRIES ALLOWED

or

NEW ENTRIES BLOCKED — US CPI

Include upcoming 24-hour table.

Show provider conflict.

Show required provider attribution.

Calendar outage must display:

CALENDAR UNAVAILABLE
NEW ENTRIES BLOCKED

not:

NO NEWS.

================================================================================
87. DASHBOARD STRATEGY PANEL
================================================================================

Separate:

ACTIVE STRATEGIES

momentum_continuation
pullback_continuation
range_breakout
statistical_reversion
volatility_expansion
microstructure_acceleration

and:

RETIRED STRATEGIES

failed_breakout_fade
support_resistance_reaction

For active strategies show:

version
state
latest signal
latest symbol
raw confidence
evidence-adjusted score
sample count
trades
win rate
net P/L
valid avg R
profit factor
last update

For retired:

RETIRED
NO LIVE SIGNALS
NO ACTIVE TRAINING
NO PROMOTION

================================================================================
88. DASHBOARD MODEL PANEL
================================================================================

Show every registered model:

model ID
type
version
state
CURRENT
CHALLENGER
PREVIOUS_STABLE
etc.

feature schema
dataset ID

train sample
validation sample
OOS sample

created time
last inference
inference count
inference latency

calibration state
Brier score
log loss
AUC if meaningful

OOS net R
OOS drawdown

promotion result
drift state
rollback availability
checksum health

If insufficient:

INSUFFICIENT_DATA

Do not pretend strategy score recomputation is ML training.

================================================================================
89. DASHBOARD LEARNING PANEL
================================================================================

Show:

valid authoritative results
quarantined invalid results
new finalized results since last learning
last strategy-score update
last calibration
last training
last challenger
last promotion
last rollback
drift
sample sizes
effective sample sizes
fallback level

Context tables:

symbol
strategy
regime
session

================================================================================
90. DASHBOARD RAG PANEL
================================================================================

Show:

enabled
backend
embedding/vector method
indexed memories
trade memories
setup memories
rejection memories
exit memories
execution incidents
last ingestion
last retrieval
retrieval p50
retrieval p95
index health

For latest decision:

similar cases
compatible DEMO cases
memory IDs
bounded contribution

Use expandable details.

================================================================================
91. DASHBOARD ORDER PIPELINE
================================================================================

Show live funnel:

configured symbols: 3
resolved symbols
healthy symbols
analyzed
strategy candidates
signals
proposals

data rejects
news rejects
cost rejects
expected-edge rejects
correlation rejects
portfolio rejects
risk rejects
broker-validation rejects
permission rejects
re-entry-churn rejects

orders
fills
positions
closed trades

Display:

PRIMARY CURRENT BLOCKER

================================================================================
92. DASHBOARD OPEN POSITIONS
================================================================================

For each position:

symbol
direction
volume
entry
current price
SL
TP
broker P/L
initial monetary risk
current R
peak R
giveback
holding time
strategy
model
entry regime
current regime
current expected edge
expected remaining cost
management state
last action
profit protection
news state
exit latency info where relevant

================================================================================
93. DASHBOARD POSITION EXPECTANCY
================================================================================

Explicitly display:

original expected net edge

current remaining expected edge

current R

peak R

giveback

strategy thesis:
VALID / WEAKENING / INVALID

regime relationship

current recommendation:

HOLD
PROTECT
CLOSE

reason

This must reflect actual engine logic.

================================================================================
94. DASHBOARD RE-ENTRY PANEL
================================================================================

Show recent exits and:

re-entry eligible?
cooldown remaining
same-direction?
new setup fingerprint?
current expected edge
cost of new entry
threshold
reason
result

This helps detect churn.

================================================================================
95. DASHBOARD COST / RISK PANEL
================================================================================

COST:

spread
estimated commission
expected slippage
round-trip cost
estimated vs realized costs

RISK:

balance
equity
free margin
daily realized result
open monetary risk
portfolio heat
daily-loss utilization
drawdown
max positions
positions per symbol
risk limits

================================================================================
96. DASHBOARD CORRELATION PANEL
================================================================================

Three-by-three correlation matrix:

XAUUSD
GBPJPY
BTCUSD

Display:

time window
sample count

Use:

N/A

for invalid/insufficient values.

Show:

cluster exposure
currency exposure
USD-related exposure
pending risk
portfolio decision

================================================================================
97. DASHBOARD HISTORICAL DATA PANEL
================================================================================

Per symbol show:

target history:
5 years

actual bars:
earliest
latest
counts by resolution

actual ticks:
earliest
latest
count

gaps

last bootstrap
last incremental update
download status
data-quality status

================================================================================
98. DASHBOARD BROKER HISTORY PANEL
================================================================================

Show:

historical orders imported
historical deals imported
earliest record
latest record
deduplication state

origin breakdown:

Adaptive Scalper
external/manual
unknown

Do not attribute external trades to an internal strategy.

================================================================================
99. DASHBOARD JOURNAL PANEL
================================================================================

Show recent:

signals
rejections
permission decisions
entries
position reviews
stop changes
exits
re-entry decisions
learning events

Show top recent rejection reasons.

Allow filtering by:

symbol
strategy
decision type
time

================================================================================
100. DASHBOARD PERFORMANCE
================================================================================

Separate evidence tabs:

BROKER DEMO CONFIRMED
PAPER
BACKTEST

Never mix them.

For DEMO display:

trades
gross P/L
net P/L
valid avg R
win rate
profit factor
drawdown
cost
average hold

Break down by:

symbol
strategy
regime
session
exit reason

Do not claim profitability.

================================================================================
101. DASHBOARD DECISION QUALITY
================================================================================

Display aggregated:

entry quality
exit quality
execution quality
cost quality
risk compliance
data quality
journal completeness

and recent reviewed decisions.

Make clear:

Decision Quality ≠ Profit.

================================================================================
102. DASHBOARD EXECUTION
================================================================================

Show:

recent order states
broker order IDs
deal IDs
pending orders
UNKNOWNs
last reconciliation
reconciliation findings
last broker error
execution latency
application-close latency

Never display credentials.

================================================================================
103. DASHBOARD EVENT STREAM
================================================================================

Show deduplicated:

INFO
WARNING
BLOCKED
ERROR
CRITICAL

Examples:

NEWS BLOCK ENTERED
NEWS BLOCK CLEARED
ORDER FILLED
POSITION PROFIT PROTECTED
ADAPTIVE CLOSE
REENTRY BLOCKED
MODEL CHALLENGER CREATED
MODEL REJECTED
RAG DEGRADED
RECONCILIATION ACTION
KILL SWITCH ENGAGED

Do not spam identical messages every cycle.

================================================================================
104. DASHBOARD MUST BE AN OBSERVER
================================================================================

The dashboard is NOT the trading engine.

If browser closes:

trading engine
position manager
reconciliation
risk
news
journal

must continue.

Dashboard may provide explicit operator actions such as kill-switch controls,
but these must call audited application APIs.

It must never bypass permission/risk.

================================================================================
105. CONFIDENCE QUALITY DISPLAY
================================================================================

Do not display only:

BUY 82%

Prefer:

strategy confidence
model probability
calibration quality
model sample
similar DEMO cases
RAG evidence state
expected net edge
permission result

Expose uncertainty.

================================================================================
106. WHY-NO-TRADE
================================================================================

Implement:

CLI:

why-no-trade

and dashboard panel.

Example:

symbols configured: 3
healthy: 3
strategies evaluated: 18
signals: 2
proposals: 1

Rejected:

news: 0
cost: 1
correlation: 0
risk: 0

orders: 0

Primary blocker:

EXPECTED_NET_EDGE_INSUFFICIENT

Show supporting numbers.

================================================================================
107. HEALTH SEMANTICS
================================================================================

Use:

HEALTHY
DEGRADED
NEW_ENTRIES_BLOCKED
TRADING_BLOCKED
CRITICAL

Examples:

RAG unavailable:
DEGRADED

calendar unavailable:
NEW_ENTRIES_BLOCKED

kill switch:
TRADING_BLOCKED

dangerous UNKNOWN:
TRADING_BLOCKED

database corruption:
CRITICAL

broker disconnected:
TRADING_BLOCKED or CRITICAL

================================================================================
108. CLI
================================================================================

Create coherent CLI, conceptually:

adaptive-scalper doctor
adaptive-scalper status
adaptive-scalper health

adaptive-scalper symbols

adaptive-scalper scan
adaptive-scalper analyse XAUUSD

adaptive-scalper paper
adaptive-scalper demo

adaptive-scalper dashboard

adaptive-scalper strategies

adaptive-scalper models
adaptive-scalper learning status
adaptive-scalper learning scores
adaptive-scalper learning run

adaptive-scalper rag status
adaptive-scalper rag stats
adaptive-scalper rag similar XAUUSD
adaptive-scalper rag rebuild-index
adaptive-scalper rag verify-index

adaptive-scalper news status
adaptive-scalper news refresh
adaptive-scalper news upcoming

adaptive-scalper history status
adaptive-scalper history sync
adaptive-scalper broker-history import

adaptive-scalper journal recent

adaptive-scalper reconcile

adaptive-scalper why-no-trade

adaptive-scalper kill-switch status
adaptive-scalper kill-switch engage
adaptive-scalper kill-switch clear

adaptive-scalper backtest
adaptive-scalper walk-forward
adaptive-scalper monte-carlo

Actual final names may be adjusted, but documentation must match implementation.

================================================================================
109. LOGGING
================================================================================

Create:

structured JSONL logs

and:

human-readable rotating logs.

Include:

UTC timestamp
severity
subsystem
event
symbol
strategy
decision ID
proposal ID
order ID
reason
exception

Do not log:

passwords
secrets
sensitive credentials

Rate-limit repetitive messages.

================================================================================
110. MT5 GATEWAY BOUNDARY
================================================================================

MetaTrader5 imports must remain isolated to gateway implementation.

Business logic consumes internal broker-independent types.

Centralize:

MT5 initialize
shutdown
account
terminal
symbol info
ticks
bars
orders
deals
positions
history
send
modify
close
time conversion

This supports mocks and tests.

================================================================================
111. TIME
================================================================================

UTC is internal canonical time.

Do not derive broker clock from one stale symbol.

Use reliable fresh broker information where server-time interpretation is
necessary.

Test timezone and DST behavior.

Dashboard may display local time additionally.

================================================================================
112. PROJECT STRUCTURE
================================================================================

Create approximately:

C:\AdaptiveScalperNext
│
├── adaptive_scalper/
│   ├── __init__.py
│   ├── cli.py
│   ├── app.py
│   ├── config/
│   ├── core/
│   ├── gateway/
│   ├── market/
│   ├── history/
│   ├── features/
│   ├── regimes/
│   ├── strategies/
│   ├── intelligence/
│   ├── learning/
│   ├── rag/
│   ├── journal/
│   ├── news/
│   ├── costs/
│   ├── portfolio/
│   ├── risk/
│   ├── execution/
│   ├── reconciliation/
│   ├── positions/
│   ├── persistence/
│   ├── dashboard/
│   ├── reporting/
│   └── utilities/
│
├── config/
├── data/
├── models/
├── logs/
├── tests/
├── scripts/
├── packaging/
├── docs/
│
├── MASTER_BUILD_DIRECTIVE.md
├── PROJECT_STATUS.md
├── WORKLOG.md
├── ARCHITECTURE.md
├── README.md
├── pyproject.toml
└── .gitignore

Adapt only if there is a clearly better maintainable layout.

Avoid god files.

================================================================================
113. PYTHON
================================================================================

Prefer Python 3.13 if compatible with all chosen packages.

Create a NEW virtual environment.

Do not reuse old recovery environments.

Potential dependencies:

MetaTrader5
numpy
pandas
scipy
scikit-learn
joblib
pydantic
fastapi
uvicorn
jinja2
httpx
pytest

Use only needed dependencies.

Pin tested compatible versions for release.

================================================================================
114. CONFIGURATION
================================================================================

Use validated configuration such as TOML.

Hard/non-learning configuration sections:

mode
allowed symbols
retired strategies
risk
news
execution
DEMO gate
database

Conceptually:

[market]
symbols = ["XAUUSD", "GBPJPY", "BTCUSD"]

[strategies]
retired = [
    "failed_breakout_fade",
    "support_resistance_reaction"
]

[risk]
...

[position_management]
...

[reentry]
...

[news]
primary_provider = "financecalendar"
secondary_provider = "forexfactory"
pre_high_impact_minutes = 15
post_high_impact_minutes = 30
fail_closed = true

Unsafe invalid configuration should fail startup.

================================================================================
115. STARTUP SEQUENCE
================================================================================

DEMO startup order:

1. load configuration
2. database open
3. migrations
4. DB integrity
5. persistent kill switch
6. initialize MT5
7. determine effective gateway
8. verify DEMO
9. verify terminal trading permission
10. verify broker permission
11. resolve three symbols
12. validate specifications
13. reconcile state
14. resolve UNKNOWN
15. load historical coverage
16. start incremental history sync
17. import current account broker history
18. load news cache
19. refresh calendar
20. initialize strategies
21. assert retired strategies absent
22. initialize models
23. initialize RAG
24. verify journal
25. start dashboard
26. start position manager
27. new-entry loop only if permitted

Never clear kill switch automatically.

================================================================================
116. WINDOWS LAUNCHERS
================================================================================

Create:

SETUP.bat

START PAPER.bat

START DEMO.bat

START DASHBOARD.bat

RUN BACKTEST.bat

STOP TRADING.bat

For DEMO display:

ADAPTIVE SCALPER NEXT
MODE: DEMO
REAL-MONEY EXECUTION: DISABLED

Perform doctor/reconcile checks before engine start.

Automatically open local dashboard when practical.

================================================================================
117. DEVELOPMENT PHASES — DO NOT ACTIVATE EVERYTHING IMMEDIATELY
================================================================================

Build in phases.

PHASE 1 — FOUNDATION

Git
configuration
database
migrations
MT5 gateway
DEMO gate
three-symbol resolver
kill switch
basic dashboard health

PHASE 2 — HISTORY

five-year bars
tick import
broker account history
data-quality verification
historical dashboard

PHASE 3 — CORE TRADING

features
regimes
six strategies
strategy registry
retired-strategy firewall
cost
correlation
portfolio
risk
final permission

PHASE 4 — EXECUTION

order state machine
UNKNOWN
idempotency
reconciliation
application close

PHASE 5 — POSITION INTELLIGENCE

continuous expectancy
adaptive exit
profit protection
re-entry
anti-churn

PHASE 6 — JOURNAL

immutable decision journal
complete chain linkage
decision review

PHASE 7 — NEWS

FinanceCalendar
Forex Factory fallback
cache
news gates
news dashboard

PHASE 8 — DASHBOARD

all operator panels
WebSocket
why-no-trade
event stream

PHASE 9 — RAG

memory ingestion
retrieval
firewall
decision integration

PHASE 10 — ML

observer mode
models
validation
challengers
promotion
rollback
drift

PHASE 11 — PAPER BURN-IN

PHASE 12 — CONTROLLED MT5 DEMO

PHASE 13 — PACKAGING / RELEASE

Do not activate later phases if earlier evidence integrity is broken.

================================================================================
118. NO SHOWPIECE MODULES
================================================================================

THIS IS NON-NEGOTIABLE.

Required runtime path:

MT5
↓
HISTORICAL + CURRENT DATA
↓
FEATURE ENGINE
↓
REGIME
↓
ACTIVE STRATEGIES
↓
JOURNAL
↓
RAG RETRIEVAL
↓
SELF-LEARNING/MODEL
↓
SELECTOR
↓
NEWS
↓
COST
↓
CORRELATION
↓
PORTFOLIO
↓
RISK
↓
PERMISSION
↓
MT5 DEMO
↓
RECONCILIATION
↓
POSITION EXPECTANCY
↓
ADAPTIVE EXIT
↓
JOURNAL OUTCOME
↓
LEARNING INGEST
↓
RAG INGEST
↓
FUTURE DECISIONS

For every subsystem report:

ACTIVE AND REACHABLE
PARTIALLY ACTIVE
DISCONNECTED
DEAD CODE
UNVERIFIED

A module merely existing is NOT proof of integration.

================================================================================
119. TESTING
================================================================================

Write tests while building.

Never delete failing tests merely to achieve green.

For each defect:

root cause
fix
regression test
rerun

Report actual:

passed
failed
skipped
warnings

Never fabricate results.

================================================================================
120. SYMBOL TESTS
================================================================================

Prove:

XAUUSD allowed
GBPJPY allowed
BTCUSD allowed

everything else blocked.

Test:

prefix/suffix
ambiguous alias
missing broker symbol
bad broker specification

Final permission independently blocks a fourth symbol.

================================================================================
121. RETIRED STRATEGY TESTS
================================================================================

Prove:

failed_breakout_fade cannot:

register
signal
propose
rank
train actively
promote
execute

Same for:

support_resistance_reaction

Test through:

restart
config reload
migration
model registry
RAG
packaging

Any new active proposal from them:

FAILURE.

================================================================================
122. HISTORICAL TESTS
================================================================================

Test:

five-year target request
shorter broker availability
chunking
resume
duplicate bars
duplicate ticks
gaps
invalid OHLC
incremental update
no complete redownload

Broker history:

idempotent import
order/deal deduplication
manual strategy not falsely assigned

================================================================================
123. JOURNAL TESTS
================================================================================

Test:

point-in-time decision fields immutable
outcome appended
signal → result chain complete
exit decisions linked
re-entry linked
journal feeds RAG
journal feeds learning
rejection != loss

================================================================================
124. NEWS TESTS
================================================================================

Test:

primary normalization
secondary normalization
fallback
stale cache
unavailable
duplicate event
provider conflict
UTC

For event 16:30:

16:14:59 clear
16:15:00 blocked
16:59:59 blocked
17:00:00 clear

Test:

FOMC all symbols
CPI all symbols
NFP all symbols

GBP event:
GBPJPY

JPY event:
GBPJPY

USD high impact:
XAUUSD
BTCUSD

Calendar outage:

new entries blocked

existing management continues

master kill switch not automatically engaged

Final pre-send check required.

================================================================================
125. POSITION TESTS
================================================================================

Test:

fixed initial R
peak R
restart persistence
profit giveback
early profit target
breakeven
never backward SL
setup deterioration
regime reversal
max hold
application close
already-closed race
broker reject
UNKNOWN close
latency fields

================================================================================
126. RE-ENTRY TESTS
================================================================================

Test:

automatic immediate reopen blocked

cooldown

same setup blocked

fresh setup allowed

stronger evidence required

transaction cost included

re-entry churn blocker

different direction as a genuinely new setup

================================================================================
127. EXECUTION TESTS
================================================================================

Test:

market order
pending if supported
partial
reject
timeout
UNKNOWN
late fill
duplicate request
restart UNKNOWN
reconciliation

Invariant:

UNKNOWN never blindly resends.

================================================================================
128. COST/CORRELATION/RISK TESTS
================================================================================

Test cost independently for:

XAUUSD
GBPJPY
BTCUSD

Test:

safe sizing
round-down
minimum lot risk rejection
spread
commission
slippage
expected net edge

Correlation:

insufficient data
valid sample
high relation
pending exposure

Risk:

daily loss
drawdown
open risk
max positions
per-symbol cap

================================================================================
129. ML TESTS
================================================================================

Test:

point-in-time features
no target leakage
temporal split
insufficient sample
observer mode
bounded influence
challenger
failed promotion
successful promotion
rollback
artifact checksum
feature mismatch
drift
retired strategy exclusion

================================================================================
130. RAG TESTS
================================================================================

Test:

ingestion
deduplication
retrieval
provenance
source links
rebuild
verify
failure
bounded influence

Prove RAG cannot:

execute
change risk
clear kill switch
reactivate retired strategy
change symbol allow-list

================================================================================
131. DASHBOARD TESTS
================================================================================

Test:

HTTP startup
WebSocket
state push
empty DB
no positions
open position
calendar block
calendar outage
model missing
model challenger
RAG degraded
UNKNOWN
kill switch
broker disconnect
N/A fields
history bootstrap

Dashboard must never crash due to missing optional data.

================================================================================
132. PAPER MODE
================================================================================

PAPER uses real MT5 market data when available.

No broker order_send.

Clearly label all resulting evidence:

PAPER_LIVE_DATA

Run PAPER burn-in before DEMO.

================================================================================
133. CONTROLLED DEMO VALIDATION
================================================================================

Only proceed to DEMO after:

tests acceptable
DB healthy
DEMO confirmed
symbols resolved
news system valid or correctly fail-closed
kill-switch state permits
reconciliation clean
no dangerous UNKNOWN

Run controlled cycles.

Example:

20 cycles
approximately 4-second entry cadence

Do NOT force a trade.

Zero valid trades is acceptable.

Verify all active subsystems.

================================================================================
134. DO NOT FORCE EXECUTION
================================================================================

Never lower:

strategy threshold
cost threshold
risk rules
news rules
data rules

simply to produce a trade.

If a transport diagnostic is needed, create a completely separate,
operator-confirmed DEMO diagnostic command with:

DEMO verification
tiny safe exposure
mandatory SL
mandatory TP
clear diagnostic labeling

Never run it automatically.

================================================================================
135. PACKAGING
================================================================================

After source passes:

tests
compile checks
migration checks
PAPER validation
DEMO checks where available

build a self-contained Windows release.

Use an appropriate tested packager such as PyInstaller if suitable.

Verify from clean release directory:

--help
doctor
status
dashboard
PAPER
DEMO
backtest

Include:

config
migrations
dashboard static assets
runtime requirements

Generate SHA-256.

================================================================================
136. DOCUMENTATION
================================================================================

Maintain:

MASTER_BUILD_DIRECTIVE.md

README.md

ARCHITECTURE.md

PROJECT_STATUS.md

WORKLOG.md

LEARNING_AND_MEMORY.md

NEWS_SYSTEM.md

DASHBOARD.md

SAFETY.md

RELEASE.md

HISTORICAL_DATA.md

JOURNAL.md

Documentation must match actual implementation.

================================================================================
137. ONE-MONTH CLAUDE PROJECT CONTINUITY
================================================================================

This project will likely span many Claude sessions.

PROJECT_STATUS.md must contain:

current stage
completed components
verification state
current schema version
current model state
current git commit
tests
known defects
unverified components
exact next task

WORKLOG.md records chronological major changes.

At every new Claude session:

1. read MASTER_BUILD_DIRECTIVE.md
2. read PROJECT_STATUS.md
3. read WORKLOG.md
4. inspect Git
5. run smoke tests
6. continue from exact recorded next task

Do not redesign the project every new session.

================================================================================
138. GIT PRACTICE
================================================================================

Create meaningful commits after validated stages.

Examples:

foundation
MT5 gateway
historical bootstrap
strategy system
risk/execution
news
journal
position management
dashboard
RAG
ML
release

Do not commit:

credentials
massive raw data
logs
runtime caches
venv
secrets

================================================================================
139. ACCEPTANCE CHECKLIST
================================================================================

Do not call this project complete until every relevant item is explicitly
reported:

[ ] new source repository exists
[ ] old project preserved
[ ] Git initialized
[ ] reproducible Python environment
[ ] exactly XAUUSD allowed
[ ] exactly GBPJPY allowed
[ ] exactly BTCUSD allowed
[ ] fourth symbol blocked
[ ] broker symbol aliases resolved safely

[ ] six active strategies
[ ] failed_breakout_fade retired
[ ] support_resistance_reaction retired
[ ] retired strategies cannot signal
[ ] retired strategies cannot train actively
[ ] retired strategies cannot promote
[ ] retired strategies cannot execute

[ ] MT5 gateway isolated
[ ] DEMO verified before every send
[ ] real-account new orders impossible

[ ] broker historical bars bootstrap
[ ] target five years attempted
[ ] actual coverage recorded honestly
[ ] tick history imported where practical
[ ] historical synchronization incremental
[ ] broker account orders imported
[ ] broker account deals imported
[ ] imports idempotent
[ ] external trades not falsely strategy-attributed

[ ] deterministic feature engine
[ ] regime detector
[ ] no-lookahead boundaries

[ ] risk governor sole sizing authority
[ ] minimum lot does not override safety
[ ] daily loss works
[ ] drawdown works
[ ] aggregate risk works

[ ] cost model works
[ ] expected-net-edge gate works
[ ] correlation works
[ ] portfolio heat works

[ ] order state machine
[ ] idempotency
[ ] UNKNOWN path
[ ] reconciliation
[ ] application close
[ ] already-closed race

[ ] continuous position expectancy
[ ] fixed initial R
[ ] peak R persistence
[ ] profit retracement exit
[ ] early profit target
[ ] breakeven
[ ] thesis deterioration
[ ] regime reversal
[ ] max hold
[ ] exit latency captured

[ ] re-entry is new decision
[ ] re-entry threshold stronger
[ ] anti-churn
[ ] cooldown
[ ] new costs included

[ ] FinanceCalendar primary implemented or actual limitation documented
[ ] current provider terms checked
[ ] required attribution included
[ ] Forex Factory structured fallback implemented or limitation documented
[ ] no fragile HTML calendar scraping
[ ] news caching
[ ] FOMC block
[ ] CPI block
[ ] NFP block
[ ] GBP event logic
[ ] JPY event logic
[ ] 15-minute pre window
[ ] 30-minute post window
[ ] calendar failure blocks entries only
[ ] existing positions managed during news
[ ] final pre-send news check

[ ] immutable journal
[ ] journal chains linked
[ ] journal feeds learning
[ ] journal feeds RAG

[ ] decision quality research
[ ] exit regret research
[ ] re-entry regret research
[ ] counterfactual evidence separated

[ ] local RAG
[ ] RAG provenance
[ ] RAG rebuild
[ ] RAG verify
[ ] RAG bounded
[ ] RAG cannot execute

[ ] ML observer stage
[ ] sample protection
[ ] model registry
[ ] challengers
[ ] OOS
[ ] walk-forward
[ ] cost-aware validation
[ ] promotion gate
[ ] rollback
[ ] drift
[ ] ML bounded
[ ] ML cannot modify hard safety

[ ] system dashboard
[ ] three-symbol dashboard
[ ] news dashboard
[ ] strategy dashboard
[ ] model dashboard
[ ] learning dashboard
[ ] RAG dashboard
[ ] historical coverage dashboard
[ ] broker-history dashboard
[ ] journal dashboard
[ ] position expectancy dashboard
[ ] re-entry dashboard
[ ] risk/cost dashboard
[ ] correlation dashboard
[ ] performance dashboard
[ ] execution dashboard
[ ] event stream
[ ] decision-quality dashboard
[ ] why-no-trade

[ ] no fake dashboard data
[ ] dashboard failure cannot stop position safety

[ ] PAPER verified
[ ] controlled DEMO verification performed where possible
[ ] no trade forced

[ ] exact test totals reported
[ ] failing tests not hidden
[ ] Windows package built
[ ] packaged release smoke-tested
[ ] launcher files work
[ ] SHA-256 generated
[ ] documentation matches reality

[ ] no real-money trade
[ ] no profitability claim

================================================================================
140. REQUIRED FINAL ENGINEERING REPORT
================================================================================

At project completion produce:

A. PROJECT LOCATION

B. GIT STATUS / FINAL COMMIT

C. ARCHITECTURE

D. SYMBOL MAPPINGS
XAUUSD
GBPJPY
BTCUSD

E. HISTORICAL DATA
actual coverage
bars
ticks
gaps

F. BROKER ACCOUNT HISTORY
orders
deals
coverage

G. STRATEGIES
active
retired

H. NEWS
providers
health
cache
blocks tested

I. JOURNAL
events
decision chains
learning/RAG integration

J. MODELS
baseline
observer
challengers
current
previous stable
metrics
samples

K. RAG
memories
retrieval health
provenance

L. COST MODEL

M. CORRELATION

N. RISK

O. POSITION MANAGEMENT
adaptive exit
current expectancy
re-entry
latency

P. DECISION QUALITY / REGRET RESEARCH

Q. EXECUTION
orders
fills
UNKNOWN

R. RECONCILIATION

S. DASHBOARD
local address
all working panels
WebSocket state

T. TESTS
passed
failed
skipped
warnings

U. PAPER VALIDATION

V. DEMO VALIDATION
report ONLY what was genuinely exercised

W. WINDOWS RELEASE
path
executable
hash

X. EXACT START COMMANDS

Y. KILL-SWITCH COMMAND

Z. REMAINING LIMITATIONS

Never describe untested functionality as verified.

Never claim profitability.

================================================================================
141. FINAL OPERATING PRINCIPLE
================================================================================

Adaptive Scalper Next is NOT:

"trade as frequently as possible."

It is:

"observe XAUUSD, GBPJPY and BTCUSD continuously; use six active
short-duration strategy families to form hypotheses; use valid historical
evidence, a structured decision journal, local memory and properly validated
models to improve context recognition; reject trades that do not survive
news, cost, correlation, portfolio, risk, account, broker and execution
constraints; manage open positions faster than the new-entry scanner;
continuously reassess whether remaining expected value justifies holding;
protect accumulated profit when the original thesis deteriorates; close
safely rather than blindly waiting for SL or TP; consider re-entry only as
a stronger new opportunity after paying all new costs; reconcile every
execution to broker truth; learn only from valid evidence; and expose the
entire process through a real-time local dashboard."

A correct FLAT decision is valid.

A correct NO TRADE is valid.

A losing trade can be a correctly executed decision.

A winning trade can still be a poor decision if safeguards were violated.

Better learning means:

better evidence
better calibration
better context recognition
better exits
better cost estimation
better memory
better refusal decisions

It does NOT mean:

higher leverage
more risk
more trades
weaker safety
automatic re-entry
ignoring news
ignoring transaction costs.

================================================================================
142. START NOW
================================================================================

Begin immediately.

STEP 1:
Create C:\AdaptiveScalperNext.

STEP 2:
Initialize Git.

STEP 3:
Save this entire directive as:

MASTER_BUILD_DIRECTIVE.md

STEP 4:
Create:

PROJECT_STATUS.md
WORKLOG.md
ARCHITECTURE.md
README.md

STEP 5:
Create the repository skeleton.

STEP 6:
Create a new Python environment.

STEP 7:
Implement validated configuration and SQLite migrations.

STEP 8:
Implement MT5 gateway + DEMO hard interlock.

STEP 9:
Resolve XAUUSD, GBPJPY and BTCUSD safely.

STEP 10:
Implement historical bootstrap and broker account-history import.

STEP 11:
Implement tests immediately.

STEP 12:
Continue through the defined development phases.

Do not stop after generating scaffolding.

Do not give me a hypothetical architecture and call the task done.

Build the project.

Test each phase.

Fix failures.

Update PROJECT_STATUS.md and WORKLOG.md continuously.

When a stage is complete, continue to the next safe stage.

Only stop for truly unavoidable user intervention.

================================================================================
END OF AUTHORITATIVE MASTER DIRECTIVE
================================================================================
