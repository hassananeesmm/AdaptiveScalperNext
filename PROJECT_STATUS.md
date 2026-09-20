# PROJECT STATUS

Update this file continuously as work happens (not retroactively), per
`CLAUDE.md` rule 10 and `MASTER_BUILD_DIRECTIVE.md` §137. This file
describes the CURRENT real repository state — when a rewrite happens,
prior phrasing that no longer matches reality is replaced, not left
alongside the update as a contradiction.

Status tags used below, applied per component:

- **IMPLEMENTED** — code exists and is believed correct.
- **CONNECTED** — wired into a real call path (not just importable/dead code).
- **TESTED (fake/mock)** — covered by deterministic tests (FakeGateway, tmp SQLite).
- **TESTED (live)** — exercised against this machine's real MT5 terminal.
- **UNVERIFIED** — no automated test exercises it yet.
- **KNOWN DEFECT** — see BUG_BACKLOG.md for detail.

## Project

Adaptive Scalper Next

## Repository

C:\AdaptiveScalperNext (GitHub: `hassananeesmm/AdaptiveScalperNext`, branch `main`)

## Current phase

Directive §117 PHASE 1 (FOUNDATION) and PHASE 2 (HISTORY) are complete
and live-verified. PHASE 3 (CORE TRADING) is now substantially complete
and live-verified end to end: feature engine, regime classifier, six
active strategies + retirement firewall, the immutable decision journal,
the keyless news system, the cost/expected-net-edge gate, correlation/
portfolio exposure tracking, the risk governor, and the COMPOSED final
permission gate (directive §36) all exist and were verified together in
one real pipeline run against the live DEMO account and real market
data (see "Composed final permission gate" below). Phases 4-13
(execution, position management, RAG, ML, backtesting, full dashboard,
release) have not started — `order_send` does not exist anywhere in
this codebase and remains deliberately locked until Phase 4's
dependencies (execution state machine, idempotency, UNKNOWN handling,
reconciliation) exist.

## Completed components

### Development safeguards (IMPLEMENTED, CONNECTED, TESTED (fake))

- `.claude/hooks/guardrails.py` — deterministic, stdlib-only PreToolUse
  hook (HARD BLOCK: writes/deletes targeting the sibling `C:\AdaptiveScalper`
  project, real-money/live-trading enablement, hardcoded secrets, full
  test-suite deletion, destructive ops outside the project root,
  permission-bypass attempts; WARNING/ask: individual test deletion,
  force push, `git reset --hard`, `git clean -f`, safety-boundary edits,
  writes outside the project root). `tests/test_guardrails.py`.
- `.claude/claude-security-guidance.md` + `.claude/security-patterns.json`
  — project-specific guidance/patterns for the security-guidance plugin.
- `.claude/hookify-templates/` + `scripts/setup_claude_hooks.ps1` —
  regeneration/self-test so safeguards survive a fresh clone/machine.

### `adaptive_scalper/config/` (IMPLEMENTED, CONNECTED, TESTED (fake))

Hard safety constants (`ALLOWED_CANONICAL_SYMBOLS`, `RETIRED_STRATEGY_KEYS`,
`ALLOWED_MODES`) plus a pydantic-validated, fail-closed TOML config loader.
`tests/test_config.py` (24 tests).

### `adaptive_scalper/persistence/` (IMPLEMENTED, CONNECTED, TESTED (fake))

SQLite connection helper (WAL, foreign_keys ON) and a transactional,
idempotent migration runner. Schema at version 8 — see "Schema version"
below for the migration list. `tests/test_persistence.py` (7 tests) +
`tests/test_migration_parser.py` (12 tests).

`_split_statements()` is now built on `sqlite3.complete_statement()` —
SQLite's own statement-boundary oracle — rather than a naive `;`-split;
this is what let migration `0006_journal.sql` add a `CREATE TRIGGER ...
BEGIN ... END` immutability guard at all. The previously-tracked "naive
splitter" defect is fully fixed; see BUG_BACKLOG.md's "Fixed" section for
the exact implementation and test list.

### `adaptive_scalper/core/` — kill switch, operator authority, permission slice

- `kill_switch.py` (IMPLEMENTED, CONNECTED, TESTED (fake), 36 tests) —
  **fail-closed**: `get_state()` returns `UNINITIALIZED` (no row) or
  `INVALID` (unparseable row) rather than defaulting to "safe to trade";
  `blocks_new_entries` is True for everything except an explicit,
  persisted `DISENGAGED`. State + audit-row writes are wrapped in one
  real transaction (`_write()`: `BEGIN`/two inserts/`COMMIT`,
  `ROLLBACK` on any `sqlite3.Error`) — never observable out of sync.
  `engage()` has no role restriction (any safety component may trip it).
  `clear()`/`bootstrap()` require an `OperatorAuthority` instance, not a
  bare role string. **This is the REAL runtime kill switch** — its state
  in `data/adaptive_scalper.sqlite3` must never be auto-cleared by
  startup, migration, restart, ML, or RAG. If it is ever found
  `UNINITIALIZED`/`INVALID`/`ENGAGED` when preparing to run PAPER/DEMO,
  the correct response is to show the operator the exact `kill-switch
  bootstrap`/`clear` command and stop, not to call it automatically.
- `operator_authority.py` (IMPLEMENTED) — typed capability object.
  **Honesty note**: this is NOT cryptographic access control — Python
  cannot stop arbitrary code from constructing one. The real boundary is
  code review + which packages ever import this module (intended
  construction sites: CLI operator commands, the dashboard's eventual
  authenticated operator-action endpoint). Documented as such in the
  module docstring; future hardening (session/token verification inside
  `__init__`) is a drop-in upgrade that doesn't change any caller.
- `permission.py` — the kill-switch slice: blocks `NEW_ENTRY` with
  `BLOCK_KILL_SWITCH` for anything except `DISENGAGED`; always allows
  `POSITION_MANAGEMENT`/`RECONCILIATION`. Now COMPOSED, along with every
  other independent gate, into `core/final_permission.py` — see that
  section below for the actual complete final trade-permission gate
  (directive §36). This file's own function remains a reusable building
  block the composed gate calls, not a separate incomplete path.

### `adaptive_scalper/gateway/` (IMPLEMENTED, CONNECTED where noted)

- `types.py` / `protocol.py` — broker-independent dataclasses and the
  `Gateway` Protocol: `initialize`/`shutdown`/`account_info`/
  `terminal_info`/`symbols_get`/`symbol_info`/`symbol_info_tick`/
  `copy_rates_from_pos`/`copy_rates_range`/`copy_ticks_range`/
  `history_orders_get`/`history_deals_get`/`last_error`. `SymbolSpec.
  trade_mode` preserves MT5's full 5-state `ENUM_SYMBOL_TRADE_MODE`
  (`DISABLED`/`LONGONLY`/`SHORTONLY`/`CLOSEONLY`/`FULL`) with
  `allows_new_long`/`allows_new_short`/`allows_any_new_exposure`/
  `allows_close` helper properties. `Tick` carries `time_msc` (default 0)
  for millisecond-resolution tick-history dedup. Protocol deliberately
  excludes `order_send`/`order_check`/`positions_get`/`orders_get`/close/
  modify — waiting on the execution state machine, idempotency, and
  reconciliation to exist first (directive §118 "no showpiece modules").
- `mt5_gateway.py` — TESTED (live) for `initialize`/`account_info`/
  `terminal_info`/`symbols_get`/`symbol_info`/`symbol_info_tick`/
  `copy_rates_range`/`copy_ticks_range`/`history_orders_get`/
  `history_deals_get` on IC Markets Global. `copy_rates_from_pos` (the
  original position-based bar fetch, superseded in practice by
  `copy_rates_range` for the history bootstrap) is implemented but
  UNVERIFIED — no test, live or fake, exercises it.
- `fake_gateway.py` — deterministic in-memory implementation backing all
  fake-based gateway tests; records `rates_range_calls`/`ticks_range_calls`
  for resumability assertions.
- `synchronized_gateway.py` — NEW. `SynchronizedGateway` wraps any
  `Gateway` and serializes every call through one `threading.RLock`,
  fixing the MT5-concurrency gap an external review flagged: MetaTrader5's
  underlying calls are not documented as safe for concurrent multi-thread
  use, and multiple call sites (dashboard worker threads today; the
  entry scanner/position manager/reconciliation/history jobs as they're
  built) must never issue uncontrolled concurrent calls against one
  terminal connection. `cli.py`'s `dashboard` command now wraps the real
  `Mt5Gateway` in this before injecting it into the dashboard. TESTED
  (fake, `tests/test_synchronized_gateway.py`, 3 tests) — including a
  genuine multi-thread concurrency test (not just delegation) with a
  companion unsynchronized-baseline test proving the probe can actually
  detect a missing lock. Not yet consumed by anything beyond the
  dashboard, since the other call sites don't exist yet.
- `demo_gate.py` — `verify_demo_before_order()`. TESTED (fake, 12 tests)
  + TESTED (live, part of `test_mt5_gateway_live.py`). Re-fetches fresh
  state every call; fails closed to the directive §36 vocabulary.
- `symbol_resolver.py` — exact + capped-affix alias matching. TESTED
  (fake, 18 tests) + TESTED (live: XAUUSD/GBPJPY/BTCUSD all EXACT_MATCH
  on IC Markets Global). **Fixed defect** (caught by external security
  review before this was ever pushed further): the alias regex
  previously alias-matched `XAUUSDT` (a genuinely different instrument —
  gold priced in Tether — on many brokers) to `XAUUSD`. Fixed by scoping
  case-insensitivity to just the canonical-symbol portion of the pattern
  and requiring a no-delimiter suffix to be lowercase-only (typical
  broker markers) rather than any case. Regression test:
  `test_currency_like_suffix_does_not_alias_match`.
- `symbol_validation.py` — a name match alone does not mean a symbol is
  safely executable. Four independent checks, each failing closed on any
  doubt:
  1. `validate_resolved_symbol()` — `trade_mode != DISABLED`, sane
     contract spec, asset identity (see below), a live quote with
     `ask >= bid > 0`, and quote freshness within a configurable max age
     (default 30s — this is the lenient bootstrap/informational check,
     see #2 for the strict execution-time one). Reasons:
     `NO_SYMBOL_INFO`/`TRADING_DISABLED`/`INVALID_CONTRACT_SPEC`/
     `ASSET_IDENTITY_MISMATCH`/`NO_QUOTE`/`INVALID_QUOTE`/`STALE_QUOTE`/
     `VALID`. Persisted alongside the `symbol_mapping` row (migration
     `0003`).
     - **Asset identity** (`EXPECTED_IDENTITY`, external review fix #1):
       positively confirms broker metadata matches the expected
       instrument, not just the resolved name. Per-symbol spec, NOT a
       uniform rule — `base_currency` is checked only for true FX/metal
       pairs (XAUUSD, GBPJPY), where MT5's `currency_base` is reliable.
       For BTCUSD it is deliberately skipped: **live-verified against
       this project's real IC Markets DEMO terminal, BTCUSD reports
       `currency_base=currency_profit=currency_margin="USD"`** — the
       broker settles/margins the crypto CFD entirely in USD and doesn't
       use `currency_base` to name the crypto asset. A first draft that
       required `currency_base="BTC"` would have permanently failed
       closed on the genuine, correctly-resolved BTCUSD instrument on
       this real broker — caught and fixed before ever being committed
       (see BUG_BACKLOG.md's "Fixed" section). BTCUSD identity instead
       rests on `profit_currency="USD"` plus the broker's own
       `description` containing "bitcoin"/"btc" — two independent
       signals. All three canonical symbols live-verified as `VALID`
       against the real broker after the fix.
  2. `validate_execution_quote()` — NEW, external review fix #2.
     Deliberately SEPARATE from and stricter than #1's tick check, for
     use immediately before `order_send` once that exists: missing tick
     → BLOCK, zero/unusable timestamp → BLOCK (not silently skipped, as
     #1 does for bootstrap purposes), implausibly-future timestamp →
     BLOCK, stale (default max age 5s, vs. #1's 30s) → BLOCK, invalid
     bid/ask → BLOCK. Takes a `Tick`, not a gateway, so it stays a pure,
     trivially-testable function — the caller fetches the freshest
     possible tick immediately before calling it.
  3. `validate_direction_for_new_exposure()` — NEW, external review
     fix #3. For NEW exposure only: `DISABLED`/`CLOSEONLY` → BLOCK both
     directions; `LONGONLY` → BUY only; `SHORTONLY` → SELL only;
     `FULL` → either. Risk-reducing closes are unaffected — this function
     is never consulted for a close.
  4. Contract-spec sanity (part of #1): contract size/volume
     min·max·step/point/tick size·value all positive, `volume_max >=
     volume_min`.

  TESTED (fake, `tests/test_symbol_validation.py`, 48 tests: the original
  24 plus 24 new for the identity/execution-quote/direction fixes).
  **UNVERIFIED live** for `validate_execution_quote`/
  `validate_direction_for_new_exposure` (pure functions, no gateway I/O
  to live-test against) — the asset-identity portion of
  `validate_resolved_symbol` IS live-verified (see above).
- `tests/test_mt5_gateway_live.py` — self-skipping (skips cleanly, does
  not fail, when no MT5 terminal is available). See "Live MT5
  environment" below for its last real run.

### `adaptive_scalper/history/` — five-year MT5 historical bootstrap (IMPLEMENTED, CONNECTED, TESTED (fake), TESTED (live))

Directive sections 46-50. Chunked, resumable, idempotent download of bar
and tick history for whichever canonical symbols are currently
broker-resolved.

- `resolutions.py` — `SUPPORTED_BAR_RESOLUTIONS` (M1/M2/M3/M5/M15) and
  seconds-per-bar, the single source other history modules import rather
  than re-declaring.
- `store.py` — `insert_bars`/`insert_ticks`: `INSERT OR IGNORE` against
  migration `0004`'s `UNIQUE(canonical_symbol, resolution, ts_utc)` (bars)
  / `UNIQUE(canonical_symbol, ts_msc)` (ticks) constraints, so re-importing
  an already-covered range is a verified no-op, not just an assumption.
- `jobs.py` — `historical_import_jobs` checkpoint rows. A restart resumes
  a `PENDING`/`IN_PROGRESS` job from its persisted `cursor_utc`, never
  `requested_start_utc`. `get_or_create_job()` extends `requested_end_utc`
  forward (incremental resync, re-opening a `COMPLETE` job) but rejects
  widening `requested_start_utc` backward — tracked as BUG_BACKLOG.md #3,
  not silently mishandled.
- `coverage.py` — `historical_bar_coverage`/`historical_tick_coverage`:
  earliest/latest/count recomputed from the authoritative `bars`/`ticks`
  tables after each chunk. Gap counting is deliberately naive (actual vs.
  a perfectly continuous series) — weekends/session closures show up as
  "gaps" by design (directive section 46: preserve them, don't fill them),
  reported as informational coverage, never treated as a defect to fix.
- `bootstrap.py` — `bootstrap_bars`/`bootstrap_ticks`/`bootstrap_symbol`/
  `bootstrap_all`. Documented, deliberate scope decision (directive
  section 48's "document exact decision"): bars target a full
  `DEFAULT_BAR_YEARS = 5`; ticks default to `DEFAULT_TICK_DAYS = 30`, not
  5 years — full 5-year raw MT5 tick history for these three symbols would
  plausibly run into hundreds of millions of rows, which directive section
  48 explicitly permits scoping down rather than pretending was obtained.
  Both are caller-configurable, not hardcoded policy. Truncation-safety:
  each chunk advances the cursor only past the last bar/tick actually
  received (never blindly to the chunk boundary), so a broker response cap
  mid-chunk is handled correctly instead of silently skipping data.
- `account_history.py` — imports the CONNECTED ACCOUNT's own order/deal
  history (directive §51-52), distinct from bar/tick market data.
  Deduplicated by `(login, server, ticket)`. Every imported row labeled
  `origin='BROKER_ACCOUNT_HISTORY'`, `strategy_attribution='UNKNOWN'` —
  never inferred from outcome, never anything more specific, since no
  decision journal exists yet to prove real provenance. `type`/`state`/
  `entry` store MT5's raw `ENUM_ORDER_TYPE`/`ENUM_ORDER_STATE`/
  `ENUM_DEAL_ENTRY` integer codes, undecoded — thin import layer, not an
  analysis layer. Migration `0005`: `broker_account_orders`,
  `broker_account_deals`.
- CLI: `history bootstrap`/`history status`, `broker-history
  import`/`broker-history status`. `history bootstrap` is best-effort per
  symbol/resolution — one symbol's broker-resolution failure or
  mid-download error is reported in the JSON output and does not abort
  the others.
- `tests/test_history_bootstrap.py` (13 tests) + `tests/test_account_history.py`
  (7 tests), fake-gateway only: storage idempotency (bars, ticks, orders,
  deals), single- and multi-chunk completion, coverage correctness
  including a constructed gap, resumption after a simulated mid-run
  failure (asserts the resumed run's first request starts exactly at the
  persisted checkpoint), a same-process-restart variant using a fresh
  `sqlite3.Connection` to the same file, completed-job rerun making zero
  further gateway calls, `get_or_create_job`'s forward-extend/
  backward-reject behavior, `bootstrap_all` only touching symbols present
  in its canonical-to-broker map, two different accounts' identical
  ticket numbers not colliding.
- **TESTED (live)**: `copy_rates_range`/`copy_ticks_range` verified
  directly against the real IC Markets terminal. A full `history
  bootstrap --no-ticks` run completed for all 3 symbols × 5 resolutions
  with zero errors. Actual recorded coverage (directive section 50: never
  claim "5 years loaded" unless true — this is the honest result, not the
  5-year target): M15 reached the furthest back (~4 years for
  GBPJPY/XAUUSD); M1/M2/M3/M5 landed far short of 5 years (as little as
  ~101 days for XAUUSD M1). Bar counts cluster near ~100,000 per
  symbol/resolution for the finer timeframes, consistent with the broker
  retaining roughly a fixed NUMBER of bars per resolution rather than a
  fixed calendar window — a real broker-side retention policy, not a bug
  in this codebase's chunking (gap-count math was independently verified
  self-consistent against the recorded earliest/latest/count on this real
  data). Tick bootstrap itself (not just its gateway calls) has NOT been
  run live yet — deferred given its much larger expected volume; the
  fake-tested code path is otherwise identical to the bar path.
  `broker-history import` also ran live: 2234 orders / 2222 deals
  imported on first run, 0/0 on an immediate re-run (idempotency
  confirmed live). Account login number deliberately not recorded in
  this file (account-identifying info; see WORKLOG.md's pre-commit safety
  audit for the precedent).

### `adaptive_scalper/dashboard/` (IMPLEMENTED, CONNECTED, TESTED (fake))

FastAPI app (`create_app(db_path, gateway=None)`) with one endpoint,
`GET /api/health`, composing `health.compute_health()` — the directive
§107 HEALTHY/DEGRADED/NEW_ENTRIES_BLOCKED/TRADING_BLOCKED/CRITICAL state
from database integrity + kill-switch status + optional gateway
connection state. `cli.py`'s `dashboard` command now constructs a real
`Mt5Gateway`, wraps it in `SynchronizedGateway` (see gateway section), and
injects it — so `mt5_connected` reflects real state when run for real,
rather than always reading `null`. No panel beyond health exists yet; no
WebSocket push. `tests/test_dashboard_health.py` (9 tests).

**Fixed defect found by its own test before any commit**: `create_app()`
originally took a live `sqlite3.Connection`, which crashed under
FastAPI's worker-thread dispatch. Now takes a DB path and opens a
per-request connection. See BUG_BACKLOG.md.

### `adaptive_scalper/cli.py` (IMPLEMENTED, CONNECTED)

Real subcommands only — no stub prints a placeholder (directive §118):
`doctor`, `status`, `health`, `symbols`, `kill-switch status/engage/clear`,
`history bootstrap/status`, `broker-history import/status`, `dashboard`.
TESTED (fake: `status`/`health`/`kill-switch`/`history status`/
config-error path via tmp config+DB). TESTED (live, manual smoke test,
not yet automated pytest): `doctor`, `symbols`, `history bootstrap`,
`broker-history import` all run successfully against the real IC Markets
terminal/DEMO account this session. Not implemented: every command
listed in the directive that depends on a subsystem that doesn't exist
yet (`scan`, `analyse`, `paper`, `demo`, `strategies`, `models`,
`learning *`, `rag *`, `news *`, `history sync` as a separate incremental
command — `history bootstrap` already re-run is incremental via its job
checkpoints, `journal recent`, `reconcile`, `why-no-trade`, `backtest`,
`walk-forward`, `monte-carlo`).

### `adaptive_scalper/features/` — causal feature engine (IMPLEMENTED, CONNECTED, TESTED (fake), TESTED (live))

Directive section 11. `bar_features.compute_bar_features()` takes a
strictly-ascending, non-empty `list[Bar]` and treats the LAST bar as
"now" — the list itself is the causal boundary; nothing in the function
can see beyond what's passed. Raises `FeatureError` on empty input or
out-of-order/duplicate timestamps rather than silently reordering.
Implemented fields: returns/log-returns, realized volatility, ATR
(simple mean of true range, not Wilder-smoothed) + normalized range,
momentum, velocity/acceleration, Kaufman efficiency ratio, directional
persistence, range-expansion ratio, candle body/wick ratios (which
always exactly partition the full bar range — verified algebraically and
by test), recent high/low, spread + spread percentile, movement-to-cost
(requires an optional `point_size` param — `None` without it, never a
unit-mismatched fake number), and session/hour/weekday tagging (a
descriptive UTC-hour bucket, not a performance claim). Individual fields
needing more history than provided are `None`, never fabricated.
**Explicitly NOT yet implemented** (named gaps, not silent omissions):
swing/support-resistance structure, tick-frequency-derived features
(need raw ticks, not bars), and cross-symbol correlation (belongs to the
not-yet-built correlation/portfolio module, not a single-symbol feature
engine). `compute_multi_resolution_features()` composes one snapshot per
resolution (directive section 12: no resolution is privileged).

`tests/test_bar_features.py` (23 tests): input validation, insufficient-
data fields correctly `None`, a no-lookahead regression test (mutating
bars beyond a computed prefix cannot change that prefix's snapshot),
hand-checked formula correctness (efficiency ratio = 1.0 for a perfect
trend / near-0 for a choppy alternating series, directional persistence,
body+wick ratios summing to exactly 1.0, range expansion, spread
percentile, movement-to-cost with/without point_size), session bucketing,
schema version/timestamp recording, multi-resolution composition.
**TESTED (live)**: ran against the real 100,000-row XAUUSD M1 bar history
bootstrapped earlier this session — completed instantly, all fields
populated with sane values, no crash on the full real dataset.

### `adaptive_scalper/regimes/` — deterministic regime classification (IMPLEMENTED, CONNECTED, TESTED (fake), TESTED (live))

Directive section 13. `classify_regime()` is a pure, stateless function
of one `FeatureSnapshot` — TRENDING_UP/TRENDING_DOWN/RANGE/COMPRESSION/
VOLATILITY_EXPANSION/BREAKOUT/ERRATIC/UNKNOWN, each with a heuristic
0..1 confidence (documented as heuristic, not a calibrated probability)
and a `reason` string. Missing underlying feature data (`efficiency_ratio`/
`range_expansion_ratio` is `None`) resolves to `UNKNOWN`, never a guess.
`RegimeTracker` adds the hysteresis the directive explicitly requires
("do not close a position solely because one noisy observation briefly
flips regime"): its `confirmed_regime` only changes after
`min_confirmations` consecutive raw classifications agree on the same
new regime — a single noisy bar cannot flip it. `REGIME_VERSION` is
persisted on every classification for future journal/decision-chain
linkage once the journal exists.

`tests/test_regime_classifier.py` (22 tests): every regime branch
(missing-data → UNKNOWN, wide+decisive → BREAKOUT, wide+choppy → ERRATIC,
wide+no-direction → VOLATILITY_EXPANSION, narrow → COMPRESSION,
high-efficiency+persistent → TRENDING_UP/DOWN, low-efficiency → RANGE,
ambiguous middle → RANGE default), confidence always in `[0,1]`, every
returned regime is a known state, and `RegimeTracker`'s hysteresis
(does-not-flip-on-one-observation, flips-after-N-confirmations,
candidate-streak-resets-on-a-different-candidate,
streak-resets-when-a-raw-observation-matches-the-currently-confirmed-
regime-again, rejects `min_confirmations < 1`).
**TESTED (live)**: walked causally through the last ~2000 real XAUUSD M5
bars (bootstrapped earlier this session) computing features + regime at
each step — completed with no crash; confirmed-regime distribution
(RANGE dominant, with real COMPRESSION/TRENDING/ERRATIC periods) matches
the intuitive expectation that a short-timeframe market spends most of
its time ranging, not trending.

### `adaptive_scalper/strategies/` — six active strategies + retirement firewall (IMPLEMENTED, CONNECTED, TESTED (fake), TESTED (live))

Directive sections 9, 90, 121. Common `Strategy` protocol
(`base.py`): `evaluate(features, regime) -> StrategySignal | None`.
`StrategySignal` structurally has NO monetary/volume field — only PRICE
distances (`stop_distance`/`target_distance`, e.g. ATR multiples) —
enforcing directive section 9's "a strategy must never decide money
risk, volume, ..." at the type level, not just by convention.
`__post_init__` validates direction ∈ {BUY, SELL}, confidence ∈ [0,1],
both distances positive.

**Retirement firewall** (`registry.py`): `StrategyRegistry.register()`
checks every key against `RETIRED_STRATEGY_KEYS`
(`config/constants.py` — the single hardcoded source of truth) on every
call and raises `RetiredStrategyError` unconditionally — there is no
configuration flag, restore path, or one-time gate a retired key could
slip through. `build_active_registry()` (package `__init__.py`) is the
single source of truth for which strategies are active: exactly the six
directive-named families, freshly constructed on every call (no shared
mutable module state a "restart" could leak state through).

Six strategies, each a self-contained, deterministic, regime-gated rule
set (Stage 0 per directive §61 — no ML/learning influence yet):
- `momentum_continuation` — rides a TRENDING_UP/DOWN regime; confidence
  scales with regime confidence × efficiency ratio.
- `pullback_continuation` — enters a confirmed trend on a short-term
  counter-move (latest bar against the trend, longer momentum still
  confirms it) rather than chasing the extreme.
- `range_breakout` — trades the decisive direction of a BREAKOUT-regime
  bar (the same signal the regime classifier itself used).
- `statistical_reversion` — fades price within `proximity_threshold` of
  the recent high/low, only in RANGE/COMPRESSION.
- `volatility_expansion` — trades candle wick-rejection direction in a
  VOLATILITY_EXPANSION regime (long lower wick → BUY, long upper → SELL).
- `microstructure_acceleration` — short-horizon signal when velocity and
  acceleration agree in sign and are large relative to ATR; excludes
  COMPRESSION/ERRATIC/UNKNOWN regimes as too noisy/untrustworthy for a
  short-horizon read.

`tests/test_strategies.py` (43 tests) + `tests/test_strategy_registry.py`
(21 tests, 64 total): `StrategySignal` validation including an explicit
assertion it has no money/volume field; each strategy's fire/stay-FLAT
conditions; the retirement firewall parametrized over BOTH retired keys
(not just one), proving a rejection doesn't corrupt subsequent valid
registrations, and that `build_active_registry()` contains exactly the
six expected keys and is disjoint from `RETIRED_STRATEGY_KEYS`.
**TESTED (live)**: ran the full active registry (all six strategies)
against 3000 real, causally-walked XAUUSD M5 bars (18,000 strategy×bar
evaluations) — zero crashes; signal frequency varied sensibly by
strategy (microstructure_acceleration/statistical_reversion fired most
often, matching the earlier finding that RANGE is the dominant confirmed
regime; momentum/pullback/breakout/volatility_expansion fired rarely,
matching their respective regimes' real rarity in this data).

### `adaptive_scalper/persistence/database.py` — migration parser rewrite (IMPLEMENTED, CONNECTED, TESTED (fake), TESTED (live))

`_split_statements()` was a naive `;`-split (see BUG_BACKLOG.md's now-
fully-fixed entry). Replaced with a `sqlite3.complete_statement()`-based
scanner: scan character by character, and at every `;` test whether the
accumulated buffer is a complete statement per SQLite's own C-library
boundary oracle (comment- and string-literal-aware, and correctly
tracks `CREATE TRIGGER ... BEGIN ... END` nesting). This is what let
migration 0006 (below) add an immutability-enforcing trigger at all — the
previous splitter could not have applied it correctly.
`tests/test_migration_parser.py` (12 tests): ordinary/multiple
statements, two statements on one physical line, semicolon inside a
quoted string literal, semicolon inside a comment, a multi-statement
trigger body emitted as exactly one statement, an end-to-end migration
whose trigger genuinely blocks a real `UPDATE`, transactional rollback on
a later statement's failure, incomplete-SQL raising rather than
vanishing, and idempotency.

### `adaptive_scalper/journal/` — immutable decision journal (IMPLEMENTED, CONNECTED, TESTED (fake), TESTED (live))

Directive sections 54-58. Migration `0006_journal.sql`:
`decision_chains` (one row per traceable chain) + `journal_events`
(append-only rows in chain order). 27 event types spanning
`SIGNAL_CREATED` through `LEARNING_UPDATE` (directive's full list plus
`ORDER_PENDING`/`ORDER_CANCELLED`/`ORDER_EXPIRED`), enforced by both a
database `CHECK` constraint and Python's `EVENT_TYPES` frozenset (the
Python check raises a specific `UnknownEventTypeError` before ever
reaching the database). Strongly-typed, indexed linkage columns
(`broker_symbol`, `strategy_key`, `client_request_id`, `broker_order_id`,
`broker_position_id`, `broker_deal_id`) sit alongside a `payload_json`
column for event-specific fields — no dozens-of-mostly-NULL-columns
schema for subsystems (cost, RAG, models) that don't exist yet.

**Immutability enforced at two independent layers**: the application API
(`journal/events.py`) exposes only `append_event()` — no update/delete
function exists — AND migration 0006's `trg_journal_events_no_update`/
`trg_journal_events_no_delete` triggers make a direct `UPDATE`/`DELETE`
fail at the database level too (defense in depth against a future bug in
the Python layer). `append_event()` assigns `sequence_in_chain`
deterministically (`1 + MAX(existing)`) inside a `BEGIN IMMEDIATE`
transaction together with the row insert, closing a TOCTOU race a
plain autocommit read-then-write would have left open under concurrent
callers.

`tests/test_journal.py` (18 tests): append/ordering, cross-chain
independence, linkage-field + payload round-trip, `get_events_by_type`/
`get_events_for_broker_order` query correctness, unknown-event-type
rejection (and that nothing partially writes), every directive-named
event type accepted, `get_or_create_chain` idempotency and its
symbol-mismatch guard, both immutability triggers actually firing
(`sqlite3.Error` with an "append-only" message, not just "no test caught
a problem"), the point-in-time-immutability principle itself (an earlier
event's payload is provably unaffected by a later contradicting one),
restart persistence via a fresh connection, and atomic chain-creation
handling.

**TESTED (live)**: ran the complete features → regime → strategy →
journal pipeline against 500 real, causally-walked XAUUSD M5 bars,
journaling every real `StrategySignal` the six active strategies
produced (391 `SIGNAL_CREATED` events) into the actual persistent
`data/adaptive_scalper.sqlite3` database with migration 0006 genuinely
applied (not a tmp test DB) — confirmed readable back via
`get_events_by_type()`.

### `adaptive_scalper/news/` — keyless economic news system (IMPLEMENTED, CONNECTED, TESTED (fake), TESTED (live))

Directive sections 38-44. `EconomicCalendarProvider` protocol
(`provider.py`) + normalized `EconomicEvent` (`types.py`, directive
section 39's full field list). `blocking.py` is the safety-critical,
pure-function core — no I/O, fully testable without network:
`evaluate_news_block()` checks calendar health FIRST (STALE/UNAVAILABLE
→ `BLOCK_NEWS_CALENDAR_UNAVAILABLE`, CONFLICT →
`BLOCK_NEWS_PROVIDER_CONFLICT`, unconditionally, before ever looking at
events — directive section 43's core requirement), then the systemic
FOMC/US_CPI/US_CORE_CPI/US_NFP global blockers (all 3 symbols) and the
per-symbol currency-relevance mapping (XAUUSD/BTCUSD: USD;
GBPJPY: GBP, JPY), using a 15-min-pre/30-min-post window with an
**inclusive start, exclusive end** boundary — matching directive section
41's exact worked example (16:15:00 blocked, 16:59:59 blocked, 17:00:00
clear) byte-for-byte; this boundary detail was caught wrong on the first
attempt (inclusive end) and fixed against that exact worked example
before anything else touched it.

**Honesty note on the PRIMARY provider**: the directive names
"FinanceCalendar" as the keyless PRIMARY source. A real web search
during implementation found no genuine, distinct, official, keyless,
structured-JSON service by that name. Per directive section 138's own
acceptance-checklist escape valve ("FinanceCalendar primary implemented
OR actual limitation documented"), `providers/financecalendar.py` is an
honest, explicit stub — `fetch()` always raises immediately with no
network call, documented as a real limitation, not faked. The fallback
chain treats this exactly like any other primary failure and proceeds to
SECONDARY.

- `providers/forexfactory.py` — the REAL, live-verified SECONDARY
  provider: Forex Factory's public JSON feed
  (`nfs.faireconomy.media/ff_calendar_thisweek.json`), no API key, no
  HTML scraping. Live-verified returning genuine structured events
  (schema: title/country/date/impact/forecast/previous) AND, separately,
  a genuine HTTP 429 "Rate Limited" response under repeated polling
  during development — exactly the real-world provider-failure case
  `ProviderError` exists to surface rather than silently swallow.
- `providers/cache.py` — TERTIARY last-known-good local cache
  (migration `0007_news.sql`: `news_events`, `news_provider_state`).
  Raises if never populated or if the newest cached data exceeds
  `max_age_seconds` — staleness is a real, checked failure mode, not
  assumed away.
- `providers/manual.py` — OPTIONAL operator-supplied normalized JSON
  fallback, never used automatically.
- `calendar_service.fetch_with_fallback()` — tries every live provider
  (not just the first), so PRIMARY/SECONDARY can be cross-checked for
  conflict; persists every success to cache; falls back to cache only
  when every live provider fails; reports `UNAVAILABLE` (never a silent
  empty "no news") when even the cache can't help.

`tests/test_news_blocking.py` (34 tests, no network) + 
`tests/test_news_providers.py` (26 tests, HTTP mocked) +
`tests/test_news_calendar_service.py` (8 tests) = 68 total: the exact
directive-worked 16:30-event timeline, systemic-event blocking for all
three symbols, per-symbol currency relevance, LOW/MEDIUM impact never
blocking, calendar-outage/conflict precedence over normal window logic,
every provider's success/failure/malformed-input paths, cache
staleness, and the fallback chain's provider-selection, persistence, and
conflict-detection behavior.

**TESTED (live)**: ran the complete real fallback chain
(FinanceCalendar stub → real ForexFactory fetch, 105 real events, 16
real HIGH-impact) against the actual persistent database with migration
0007 applied — correctly identified a genuine upcoming real FOMC week
(Federal Funds Rate / FOMC Economic Projections / FOMC Statement, all at
one timestamp, plus a separate FOMC Press Conference 30 minutes later).
Verified the full real timeline around that real event: ALLOW 20 minutes
before, BLOCK 10 minutes before through the Statement, still BLOCK 20-35
minutes after (correctly extended by the real, distinct Press Conference
event's own window, not a bug) — genuine real-world data exercising a
real overlapping-events case the synthetic unit tests didn't happen to
cover.

### `adaptive_scalper/costs/` — per-symbol cost model + expected-net-edge gate (IMPLEMENTED, CONNECTED, TESTED (fake), TESTED (live))

Directive section 34. Everything stays in PRICE units — the same units
`StrategySignal.stop_distance`/`target_distance` use — so cost can be
compared directly against a strategy's own hypothesis without needing
position size or account risk (which the not-yet-built risk governor
alone decides).

- `model.py`: `estimate_cost()` combines spread + commission + slippage
  + swap + an uncertainty margin (a % buffer that can only ever increase
  the effective cost bar, never reduce it) into one `CostEstimate`.
  `price_equivalent_of_monetary_cost()` converts a flat per-lot monetary
  cost (commission, swap) into an equivalent price distance using the
  SYMBOL'S OWN `trade_tick_size`/`trade_tick_value` — directive section
  34: "Do not use one generic forex cost for all markets."
- `edge.py`: `expected_gross_edge_price()` computes a standard
  expected-value estimate directly from the strategy's own
  `raw_confidence`/`stop_distance`/`target_distance` (Stage 0 — directive
  section 61, no model/RAG adjustment yet; this is the exact seam where
  a future bounded model/RAG adjustment would plug in without changing
  this function's signature). `evaluate_cost_gate()` returns `BLOCK_COST`
  when costs couldn't be determined at all (distinct from
  `BLOCK_EXPECTED_EDGE`, when costs are known but the net edge doesn't
  clear the bar) or `ALLOW`.
- `tracking.py` + migration `0008_costs.sql` (`cost_observations`):
  `record_estimated_cost()` at decision time, `record_realized_cost()`
  once (never twice — raises if already recorded) when a real fill's
  cost becomes known later, computing `prediction_error`. The
  realized-cost half is ready for when the execution layer exists to
  call it; no execution layer calls it yet.

`tests/test_cost_model.py` (13) + `tests/test_cost_edge.py` (10) +
`tests/test_cost_tracking.py` (6) = 29 tests: conversion math,
component validation, margin monotonicity, the standard EV formula
(hand-checked), all three gate outcomes, prediction-error sign in both
directions, double-recording rejection, and restart persistence.

**TESTED (live)**: computed a real cost estimate from the live XAUUSD
contract spec (`point=0.01`, `tick_size=0.01`, `tick_value=$1`) and a
$7/lot commission assumption, confirming the price-equivalent conversion
matches the unit-tested formula exactly on real data. Ran the full
features → regime → strategy → cost → edge pipeline against real M5 bar
history: low-confidence signals (~0.16, right near the strategy's own
minimum threshold) correctly resulted in `BLOCK_EXPECTED_EDGE` with
negative net edge; higher-confidence signals correctly `ALLOW`ed with
positive net edge — the standard EV arithmetic checks out by hand against
the printed numbers.

### `adaptive_scalper/portfolio/` — correlation and exposure tracking (IMPLEMENTED, CONNECTED, TESTED (fake), TESTED (live))

Directive section 35. Measurement only — hard risk ceilings belong to
the not-yet-built risk governor, which will consume this module's
outputs.

- `correlation.py`: `compute_pairwise_correlation()`/
  `compute_correlation_matrix()` compute Pearson correlation over
  ALIGNED observations only (`{timestamp: return}` dicts intersected on
  shared timestamps) — never two series of equal length zipped
  positionally, which would silently misalign data whenever coverage
  differs between symbols (live-verified this actually matters: BTCUSD's
  24/7 trading coverage vs. XAUUSD/GBPJPY's market-hours-only coverage
  gives real pairs different aligned sample sizes, not the same one).
  Reports `None` ("N/A") — never a fabricated `0.0` — for insufficient
  sample size or zero-variance (constant) series, per directive section
  35's explicit requirement. `evaluate_correlation_gate()` blocks a new
  proposal only on a GENUINELY measured high correlation with an
  already-open symbol; missing/insufficient data does not itself block
  (informational, not assumed safe or dangerous).
- `exposure.py`: `compute_exposure()` aggregates open/pending monetary
  risk, per-symbol exposure, and net currency-direction exposure (a BUY
  is long the base currency / short the profit currency) using a
  portfolio-accounting-specific canonical currency pair map — explicitly
  NOT the same as `symbol_validation.EXPECTED_IDENTITY` (that verifies
  broker-reported metadata and deliberately avoids claiming BTCUSD's
  broker `currency_base` is "BTC"; this module wants the idealized
  long/short accounting view instead, a different question). Tracks
  USD-related exposure and, via `correlated_cluster_exposure()`, combined
  exposure across symbol pairs that are BOTH open AND genuinely highly
  correlated.

`tests/test_portfolio_correlation.py` (16) + `tests/test_portfolio_exposure.py`
(14) = 30 tests: perfect correlation/anti-correlation, insufficient-sample
and zero-variance → `None` (not `0.0`), alignment correctness (including
a deliberate mismatched-timestamps case proving series are never
positionally zipped), matrix symmetry and self-correlation, the
correlation gate's block/allow/missing-data behavior, exposure
aggregation and currency-direction netting across symbols, USD-related
exposure, and cluster exposure's threshold/N/A-exclusion behavior.

**TESTED (live)**: computed real pairwise correlations across all three
canonical symbols' full M5 return history — genuinely large aligned
samples (68,855 to 95,130 observations depending on the pair, correctly
reflecting BTCUSD's different real trading-hours coverage rather than a
uniform count a naive positional zip would have produced). All three
real correlations came out low-to-modest (0.07-0.23), a plausible result
for these three instruments.

### `adaptive_scalper/risk/` — risk governor, sole sizing authority (IMPLEMENTED, CONNECTED, TESTED (fake), TESTED (live))

Directive sections 32-33. `calculate_safe_volume()` is the ONLY function
in this codebase that may compute a position's monetary size — it takes
CURRENT equity, CURRENT stop distance, and CURRENT broker contract data
only. It has **no "previous volume"/"loss streak"/"multiplier"
parameter at all**, which makes martingale/grid/revenge-sizing
structurally impossible to express through this API, not merely
discouraged — enforced by
`test_calculate_safe_volume_has_no_martingale_style_parameter`, which
inspects the actual function signature so the guarantee can't silently
erode from a future edit. Rounds DOWN to the broker's `volume_step`
(directive: never round up); if the rounded-down volume is still below
`volume_min`, REJECTS rather than bumping to the minimum (which would
risk more than `risk_per_trade_pct`).

`evaluate_risk_gate()` enforces the five hard ceilings
(`max_open_positions`, `max_positions_per_symbol`,
`max_total_open_risk_pct`, `max_daily_loss_pct`, `max_drawdown_pct`) in a
fixed check order, so the reported block reason is always the first
limit actually breached. `risk_limits_from_config()` is the only
intended construction path for `RiskLimits`, built directly from the
existing validated `RiskConfig` (directive's defaults: 0.25% per trade,
0.75% total open, 2% daily loss, 5% drawdown, 2 open positions max, 1
per symbol) — documented honestly as an import-discipline/code-review
boundary (same pattern as `operator_authority.py`'s kill-switch
boundary), since there is no ML/learning module yet that could attempt
to raise these limits.

`tests/test_risk_governor.py` (22 tests): the martingale-impossibility
signature check, safe-volume rounding/rejection/capping across a range
of contract specs, every risk-gate ceiling individually and the
first-breach-reported ordering, and `risk_limits_from_config`'s field
mapping including the directive's exact default values.

**TESTED (live)**: computed a real safe-volume result from the actual
DEMO account's real equity ($9,707.85) and XAUUSD's real contract spec
(volume_step=0.01, tick_size=0.01, tick_value=$1) with a real stop
distance from an earlier live strategy signal (4.09) — result: 0.05 lots,
$20.45 monetary risk (≈0.21% of equity after round-down, consistent with
the 0.25% target), correctly `ALLOW`ed by the risk gate with zero prior
open risk.

### External-review hardening pass (risk/cost/correlation), this session

Four findings from a further external review, addressed before composing
the final permission gate:

1. **Independent per-trade risk ceiling** (`risk/governor.py`).
   `evaluate_risk_gate()` previously trusted that `proposed_monetary_risk`
   had been correctly derived from `calculate_safe_volume()`. It now
   independently re-verifies `proposed_monetary_risk` is positive,
   finite, and `<= equity * risk_per_trade_pct / 100` — the FIRST check
   in the gate, so a tampered or miscalculated proposal is rejected
   regardless of how it reached the gate. `calculate_safe_volume()`
   remains the sole function that may ever COMPUTE a size; this is
   defense-in-depth verification, not a second sizing authority. 8 new
   regression tests, including one that proposes a deliberately oversized
   risk with an otherwise-pristine portfolio to prove this specific check
   fires.
2. **Pending risk counted in the total-risk ceiling** (`risk/governor.py`).
   `RiskGateInput` gained `current_total_pending_risk`; the total-risk
   check is now `open + pending + proposed <= max_total_open_risk_pct`
   — a resting order that could still fill is real exposure, not
   exposure that only counts once filled. Tested: pending alone reaching
   the cap, open+pending within the cap, open+pending+proposal crossing
   it.
3. **Unknown cost can never silently become zero** (`costs/model.py`).
   `estimate_cost()`'s three optional components (commission/slippage/
   swap) previously defaulted to `0.0` — a caller that forgot to measure
   one got "this cost is exactly zero" instead of an error. All four
   components are now REQUIRED keyword arguments (a caller who omits one
   gets a `TypeError`, immediately). The new
   `estimate_cost_from_evidence()` is the required real-runtime entry
   point: each component is `float | None`, and if ANY is `None` the
   function returns `None` rather than ever calling `estimate_cost()`
   with a guessed zero — `costs/edge.py`'s existing `evaluate_cost_gate()`
   already treats a `None` estimate as `BLOCK_COST`, so this closes the
   loop without needing a new block reason.
4. **Correlation N/A is not proof of safety** (`portfolio/correlation.py`).
   `evaluate_correlation_gate()` gained `treat_missing_as_blocking`
   (default `True`, and what `core/final_permission.py` uses): when
   another open/pending position exists and correlation against it is
   genuinely unresolved (N/A), the gate now blocks with
   `BLOCK_CORRELATION` conservatively, rather than treating unmeasured
   correlation as evidence of safety. `compute_pairwise_correlation()`'s
   own reporting is unchanged (still honestly `None`, never a fabricated
   `0.0`) — this is purely about what the gate DOES with that honest
   N/A. `treat_missing_as_blocking=False` preserves the old
   informational-only behavior for non-decision-making callers (e.g.
   offline analysis). When there are no open/pending positions at all,
   missing data is never blocking (nothing to conflict with).

Full suite after all four fixes: 498 passed, 0 failed, 0 skipped (17 new
tests across the three modules' existing test files).

### `adaptive_scalper/core/final_permission.py` — composed final trade-permission gate (IMPLEMENTED, CONNECTED, TESTED (fake), TESTED (live))

Directive section 36. The single point where every independent gate
built this session — canonical symbol allow-list, retired-strategy
firewall (independent final-gate defense, not just the strategy
registry's own block), DEMO account verification, the kill switch, asset
identity/directional trade mode, execution-grade quote freshness, news,
cost/expected-net-edge, correlation, and the risk governor's hard
ceilings — is wired into ONE deterministic `ALLOW`/`BLOCK_*` decision
for a proposed NEW entry, in a fixed check order (mode → symbol
allow-list → retired-strategy → DEMO → kill switch → asset identity →
direction → quote freshness → news → cost/edge → correlation → risk).
Deliberately a PURE function — every dependency is a pre-computed
result, matching every other gate already built, so the decision logic
is fully testable without a live gateway or database. No strategy, ML,
RAG, dashboard, or CLI code path may bypass this function to reach an
order.

`evaluate_and_journal_final_permission()` wraps the pure check with a
real journal write — `ENTRY_ALLOWED`/`ENTRY_BLOCKED`, directive sections
54-56 — so every permission decision becomes part of the traceable
decision chain, not computed and discarded.

**Honestly named gaps, not fabricated "always clean" defaults**:
`BLOCK_RECONCILIATION`/`BLOCK_UNKNOWN_ORDER` (no execution/reconciliation
layer yet), `BLOCK_MARGIN`/`BLOCK_BROKER_CONSTRAINT` (no live broker
order-validation call yet), `BLOCK_DUPLICATE`/`BLOCK_REENTRY_CHURN` (no
idempotency/position-history layer yet), `BLOCK_PORTFOLIO_RISK` (no
distinct cluster-level heat ceiling beyond what `BLOCK_RISK`/
`BLOCK_CORRELATION` already cover). Each is added when its real
subsystem is built — this function does not pretend they're already
covered. `order_send` still does not exist anywhere in this codebase;
an `ALLOW` from this gate is necessary but not yet sufficient to submit
an order until Phase 4's execution/idempotency/reconciliation land too.

`tests/test_final_permission.py` (23 tests): the happy path, every
individual block reason (including the retired-strategy check firing
even though the registry already independently blocks that path, and
the N/A-correlation-blocks-by-default policy exercised end to end
through the composed gate), a fixed-ordering check, and both journaling
outcomes (`ENTRY_ALLOWED`/`ENTRY_BLOCKED`) including that the journaled
event carries the strategy key.

**TESTED (live)**: ran the COMPLETE real pipeline in one script — MT5
DEMO verification, real symbol resolution/identity/direction/quote
checks, a real strategy signal found by walking real M5 bar history,
a real news check (ALLOW — no active blocking event), a real cost
estimate from the live spread + a $7/lot commission assumption, a real
correlation matrix from real aligned returns, and a real safe-volume
calculation from the live account's actual equity — then ran BOTH
through `evaluate_and_journal_final_permission()` against: (a) the REAL
persistent kill switch state (`UNINITIALIZED`, since it has never been
operator-bootstrapped in this database) — correctly returned
`BLOCK_KILL_SWITCH`, proving the gate honestly respects real safety
state rather than being bypassed for the test; and (b) a hypothetical
`DISENGAGED` `KillSwitchState` constructed only in memory for this
verification (never written to the real database — the real kill switch
was not touched) — with that one hypothetical substitution, every other
real gate passed and the result was `ALLOW`. This is the strongest
verification done this session: the entire composed decision chain
working correctly end to end against real broker/market/account data,
with the real kill switch never bypassed.

### `adaptive_scalper/execution/` — order state machine, idempotency, UNKNOWN resolution, reconciliation (IMPLEMENTED, CONNECTED, TESTED (fake), TESTED (live))

Directive sections 29-31. `order_send` still does not exist anywhere in
this codebase — this is the safety layer directive's own build order
requires to exist FIRST, built and tested before it.

- `state_machine.py`: `OrderState` (12 states) + `ALLOWED_TRANSITIONS`,
  the single source of truth for legal state changes. Broker
  acknowledgement is NOT a fill — `SUBMITTED -> FILLED` directly is
  illegal; every non-terminal active state can reach `UNKNOWN`;
  `UNKNOWN`/`PENDING_RECONCILIATION` can resolve to any terminal state or
  `PARTIAL`. `apply_transition()` raises `InvalidTransitionError` on any
  illegal jump rather than silently allowing it.
- `store.py`: `create_order()` is idempotent on `client_request_id` —
  calling it twice (e.g. after a caller can't tell if a previous attempt
  reached the broker) returns the SAME row, never a duplicate, and
  ignores the second call's parameters entirely (proven by a dedicated
  test). `transition_order_state()` is the ONLY way an order's state
  changes, always validated through the state machine first and recorded
  in `order_state_transitions` (including the initial `PROPOSED`
  creation) so an order's full lifecycle is always reconstructable, not
  just its current state.
- `unknown.py`: `resolve_unknown_order()` resolves against
  `history_orders_get()` (already implemented and live-tested).
  Explicitly, honestly limited: without a `positions_get`/`orders_get`
  (which don't exist yet — directive's own dependency order places them
  alongside `order_send`, not before), an order genuinely still resting
  with no history entry yet is correctly reported `resolved=False`
  rather than guessed at.
- `reconciliation.py`: `reconcile_positions()` — broker truth
  authoritative, detects `ORPHAN_BROKER_POSITION`/
  `MISSING_LOCAL_POSITION`/volume-or-direction `RECONCILIATION_MISMATCH`.
  `BrokerPositionSnapshot` is an explicit stand-in for `positions_get()`'s
  future real output — pure and fully testable today against constructed
  data; will not need to change when the real gateway call exists.
  `has_dangerous_unresolved_unknown()` — directive section 30's "a
  dangerous unresolved UNKNOWN may block new entries" — checks for any
  unresolved `UNKNOWN_OUTCOME` execution incident.
- Migration `0009_execution.sql`: `orders` (idempotency-keyed),
  `order_state_transitions`, `deals`, `positions`, `execution_incidents`.

`tests/test_execution_state_machine.py` (16) +
`tests/test_execution_store.py` (10) + `tests/test_execution_unknown.py`
(11) + `tests/test_execution_reconciliation.py` (15) = 52 tests: every
transition-legality edge case, idempotent creation (including that a
second call's DIFFERENT parameters are ignored), full lifecycle history
reconstruction, restart persistence, every MT5 order-state resolution
outcome (FILLED/REJECTED/CANCELLED/EXPIRED/PARTIAL/RESTING),
transient-state non-resolution, and every reconciliation finding type
including multi-position independence.

**TESTED (live)**: fetched 2,234 real orders from the live DEMO account's
order history (2,218 genuinely `FILLED`), constructed a simulated
`UNKNOWN` local order referencing one real broker ticket, and confirmed
`resolve_unknown_order()` correctly resolved it to `FILLED` with the
correct `broker_position_id` — proving the resolution logic works
against the real shape of broker history data, not just synthetic
fixtures.

## Live MT5 environment (this machine only, not guaranteed present)

This development machine has a real MT5 terminal (IC Markets Global,
server `ICMarketsSC-Demo`, account `trade_mode=0`/DEMO) installed and
logged in. `test_mt5_gateway_live.py` self-skips cleanly (does not fail)
when no terminal is reachable — re-run it to check current connectivity
rather than trusting this note, which is a point-in-time snapshot. Do
NOT assume a live terminal is present on any other machine or CI.

## Operating modes

PAPER + MT5 DEMO only. `ALLOWED_MODES` contains no third value; config
validation rejects any mode outside `{PAPER, DEMO}`. No `order_send`/
`order_check` exists anywhere in the codebase yet, so there is no
real-money execution path to disable — it has never been added, not
"added and then blocked".

## Allowed executable canonical symbols

- XAUUSD
- GBPJPY
- BTCUSD

Enforced at multiple independent layers: config validation
(`MarketConfig`), broker-name resolution (`symbol_resolver`, fails closed
on no-match/ambiguous), broker-state validation (`symbol_validation`,
fails closed on disabled/invalid-contract/asset-identity-mismatch/
no-quote/stale-quote), and (for new exposure specifically) directional
trade-mode validation (`validate_direction_for_new_exposure`). The
directive §36 final-gate `BLOCK_SYMBOL_NOT_ALLOWED` check does not exist
yet as part of a composed gate — there is no final permission gate beyond
the kill-switch slice (see `core/permission.py` above).

## Permanently retired strategies

- failed_breakout_fade
- support_resistance_reaction

Enforced at TWO independent layers now: config-validation
(`StrategiesConfig` rejects a `retired` list missing either key) and the
strategy registry (`strategies/registry.py`'s `register()` unconditionally
rejects either key, regardless of caller). Directive §121's "registry/
signal" tests are covered (parametrized over both keys, proving a
rejection doesn't corrupt the registry). NOT yet covered because the
subsystems don't exist yet: rank (no selector), train/promote (no
ML/model registry), RAG reactivation (no RAG), execute (no order path) —
each will get its own retirement-firewall regression test as that
subsystem is built, not assumed safe by extension.

## Known environment/plugin issues (non-blocking)

- The `security-guidance` plugin's LLM-powered reviewer depends on a
  machine-global venv at `~/.claude/security/agent-sdk-venv` (Python
  3.14, outside this repo/git) that can independently go stale/broken
  without any change to this repository. `scripts/setup_claude_hooks.ps1`
  checks it on every run and warns if broken. Not blocking because
  `.claude/hooks/guardrails.py` (this project's own PreToolUse gate) does
  not depend on it at all.

## Current next task

Phase 3 (CORE TRADING) is complete and live-verified end to end,
including the composed final permission gate. Phase 4 (EXECUTION) is in
progress: the order state machine, idempotency, UNKNOWN resolution, and
reconciliation LOGIC all exist and are live-verified (see
`execution/` above) — but the gateway itself still has NO
`order_send`/`order_check`/`positions_get`/`orders_get` methods, so
nothing can actually submit an order yet. Immediately next: (1) the
strategy selector (combine the six strategies' signals into one proposal
or FLAT — not simply highest raw confidence), (2) extend the gateway
with those four methods (MetaTrader5 import isolated to `mt5_gateway.py`
as always), wired through the one shared `SynchronizedGateway`, (3) wire
the execution layer's store/unknown/reconciliation modules to the real
gateway calls, all BEFORE `order_send` is ever actually invoked outside
a controlled DEMO test. A live tick-bootstrap run (currently only
fake-tested + individual live gateway-call verification) remains a
smaller open item from Phase 2.

See BUG_BACKLOG.md and this file's per-component notes for exactly what
is and isn't done; do not infer completion of anything not explicitly
marked IMPLEMENTED/CONNECTED/TESTED above.

## Current git commit

See the latest entry in WORKLOG.md for the current commit hash — this
file is updated before each commit, so the hash is recorded there rather
than duplicated (and risking going stale) here.

## Bug backlog

See `BUG_BACKLOG.md` for non-blocking known issues.

## Schema version

9 (`0001_initial`, `0002_symbol_mapping`, `0003_symbol_validation`,
`0004_historical_data`, `0005_broker_account_history`, `0006_journal`,
`0007_news`, `0008_costs`, `0009_execution`).

## Model state

None yet — no ML models implemented (Stage 0, directive §61).

## Tests

Run `pytest` for the exact current count — it changes every session and
duplicating a specific number here goes stale immediately. As of this
entry: 573 passed, 0 failed, 0 skipped, across `tests/test_environment.py`,
`test_config.py`, `test_persistence.py`, `test_migration_parser.py`,
`test_kill_switch.py`, `test_guardrails.py`, `test_demo_gate.py`,
`test_symbol_resolver.py`, `test_symbol_validation.py`,
`test_synchronized_gateway.py`, `test_dashboard_health.py`, `test_cli.py`,
`test_history_bootstrap.py`, `test_account_history.py`,
`test_bar_features.py`, `test_regime_classifier.py`, `test_strategies.py`,
`test_strategy_registry.py`, `test_journal.py`, `test_news_blocking.py`,
`test_news_providers.py`, `test_news_calendar_service.py`,
`test_cost_model.py`, `test_cost_edge.py`, `test_cost_tracking.py`,
`test_portfolio_correlation.py`, `test_portfolio_exposure.py`,
`test_risk_governor.py`, `test_final_permission.py`,
`test_execution_state_machine.py`, `test_execution_store.py`,
`test_execution_unknown.py`, `test_execution_reconciliation.py`, and
`test_mt5_gateway_live.py` (live-terminal-only, self-skipping — 7 tests,
currently connected on this machine).

## Unverified components

- `Mt5Gateway.copy_rates_from_pos` (position-based bar fetch, superseded
  by `copy_rates_range` for the history bootstrap) — implemented, no
  test, live or fake, exercises it.
- `validate_execution_quote`/`validate_direction_for_new_exposure` — pure
  functions, fake-tested only (no gateway I/O to live-test against; their
  correctness doesn't depend on live broker behavior the way asset
  identity did).
- Everything listed under "Current next task" as not yet built.
