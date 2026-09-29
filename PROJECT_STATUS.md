# PROJECT STATUS

Maintained continuously (CLAUDE.md rule 10, MASTER_BUILD_DIRECTIVE.md
section 137). This file states CURRENT truth; where older sections below
describe history, the matrix and "Current phase" here win.

**REAL-MONEY EXECUTION IS DISABLED.** PAPER + MT5 DEMO only.

Status tags (completion directive Phase 11):

- **IMPLEMENTED** — code exists and is believed correct.
- **CONNECTED** — wired into a real call path (runtime, CLI or dashboard).
- **TESTED-FAKE** — deterministic tests with FakeGateway/ChaosGateway and tmp SQLite.
- **TESTED-CLOUD** — the full suite passed on the Linux cloud runner (no MT5 there).
- **TESTED-WINDOWS** — verified on the Windows laptop (`scripts/windows_verify.ps1`).
- **TESTED-LIVE-DEMO** — exercised against the real MT5 DEMO terminal.
- **UNVERIFIED** — no automated test exercises it.
- **BLOCKED-ON-LOCAL-MT5** — needs the laptop's terminal; see LOCAL_MT5_HANDOFF.md.

Tags used in the 2026-09-25 matrix: **IMPLEMENTED**, **TESTED** (fake gateway /
temp SQLite), **VERIFIED ON WINDOWS** (ran on the laptop), **VERIFIED WITH LIVE MT5
DATA** (real terminal, real quotes/bars, no order sent), **VERIFIED ON DEMO** (a
real DEMO order), **NOT VERIFIED**, **BLOCKED**. Cloud results (docs/QA_REPORT.md,
"Cloud") never verified MT5 behaviour.

## Current phase (2026-09-29, release 0.2.5 built) -- NOT DEPLOYED

**Release 0.2.5** = `3a8df17` (this branch): P0 `Mt5QueryError` fail-closed broker truth, post-send UNKNOWN
quarantine, ASN-022..026, ASN-010 terminal pin, pending PAPER entry hardening (`f1d79f1`), V1 strategy freeze
test (`40aba67`). Full suite 1754 passed / 9 skipped / 0 failed; smoke PASSED; zip sha256
a14b75420ec2269e4d5ba21246ec665168f169bbc1cba1820d215c428f10d830. 0.2.4 (`9aed106`, peer build) was never
deployed and is superseded. **Running: still 0.2.2 (`eaa024c`) WITHOUT the P0 fix.** Operator decision: deploy
at the next flat (operator Ctrl+C, verified online backup, checkout `3a8df17`, launcher restart). Schema 29, no
migration. Research-only Strategy V2 work lives on `research/strategy-v2-20260929` (never in a release).

## Current phase (2026-09-28 night, branch `fix/integrated-demo-safety-20260928`) -- superseded by the section above

**Running (untouched):** release 0.2.2 (`eaa024c`) DEMO runtime + dashboard, started 22:07 GMT+4 from the main
checkout; schema 29; kill switch DISENGAGED (operator-set 2026-09-26); reconciliation CLEAN; 0 unresolved
incidents. At 18:55 UTC the runtime opened a natural DEMO position (BTCUSD BUY 0.16, initial risk 23.12 USD
~0.24 % of equity, `microstructure_acceleration`), which it is managing. The running 0.2.2 does NOT contain PR #4,
ASN-022, or ASN-023/024/025.

**Integration branch** `fix/integrated-demo-safety-20260928` = 0.2.3 (`5f8b208`) + PR #4 (`6f0497c`, merged
`749f3ce`) + this session's fixes. Contains every earlier lineage (PR #1, PR #2, windows-validation,
deep audit, Strategy Lab, 0.2.1 multi-position ASN-016/017, research, ASN-022). PR #3 is superseded: the lineage
already persisted pending PAPER entries; its missing guarantees were ported (ASN-024).

| Item | Evidence level |
|---|---|
| P0 `Mt5QueryError` on every MT5 collection query (None != empty) | source reviewed; fake tests (both cases x 8 calls); **live MT5 read-only** (unknown-symbol query -> `Mt5QueryError`, real empty history -> `[]`) |
| Post-send broker-truth failure -> durable UNKNOWN + incident, never resent, idempotent recovery | source reviewed; fake/chaos tests |
| ASN-023 failed/stale reconciliation blocks new entries | source reviewed; fake runtime tests (fail on old code) |
| ASN-024 pending PAPER entry typed validation + rollback | source reviewed; fake tests (fail on old code); 6 production sessions load (DB copy) |
| ASN-025 Strategy Lab on a failed positions sample | fake test |
| BUG_BACKLOG 26 stale global-block diagnostics cleared at startup | fake test |
| `[mt5] terminal_path` pinned to the IC Markets install; other install refused | fake tests; **live read-only** (pinned accepted, the other install refused) |
| Everything above | **NOT live-DEMO-verified** (no order sent in this session); NOT DEPLOYED |

Verification at `b9cc0cb`: **1716 passed / 9 skipped / 0 failed** (compileall OK; skips: 8 opt-in live-MT5 tests -- run separately with `ASN_LIVE_MT5=1`: **8 passed**, read-only -- and 1 symlink test this Windows user lacks the privilege for). Focused critical set (21 listed suites + 3 new) at `0f3f4ab`:
502 passed / 0 failed.

Deployment (operator decision): only when the broker is flat (the current BTCUSD position closes by its own
SL/TP/management), then a controlled restart of runtime + dashboard onto this branch's release; no migration.

## Audit checkpoint (2026-09-28, branch `fix/mt5-broker-truth-fail-closed-20260928`) -- historical

(Superseded by the section above. Its "deployed source remains 0.2.0" was already stale: 0.2.2 was deployed
2026-09-27 evening, see below.)

The latest 0.2.1 source lineage was re-audited against the deployed DEMO execution path. A P0 fail-closed defect
was found in `Mt5Gateway`: MetaTrader5 collection APIs use `None` for query failure, but the gateway converted
that to `[]`, making "broker truth unavailable" indistinguishable from a successful empty positions/orders/
history result. Commit `c90c22d` introduces `Mt5QueryError` and preserves genuine empty sequences while
raising on `None`. Secondary Windows/Python-3.13 verification is GREEN: compileall passed; focused
gateway/chaos/reconciliation/runtime tests **150 passed / 0 failed**; full suite **1610 passed / 8 skipped /
0 failed** (one existing third-party warning). This fix is NOT deployed and the live Windows runtime/database/
kill switch were not touched by this remote audit. The last documented deployed source remains 0.2.0
(`7f604ab`); controlled local verification and a flat operator-approved restart are required before deployment.

## Project

Adaptive Scalper Next

## Repository

`C:\AdaptiveScalperNext` on the Windows laptop; GitHub
`hassananeesmm/AdaptiveScalperNext`. Branch history: `claude/pensive-newton-tckoid`
(cloud, PR #1) -> `windows-validation` (Windows fixes W1-W4) ->
`dashboard/responsive-live-windows` (PR #2: responsive dashboard + Windows fixes
W5-W6; local branch `dashboard-review`). Nothing is merged into `main`.

## Current phase (2026-09-27 evening): release 0.2.2 DEPLOYED

Deployed with operator approval at 18:50-18:57 GMT+4 while the broker was flat: verified online backup
`data/backups/pre_0_2_2_deploy_20260927T144913Z.sqlite3`; runtime and dashboard stopped with Ctrl+C (graceful
ENGINE_STOPPED; kill switch NOT touched, still DISENGAGED); main checkout switched to `eaa024c` (detached HEAD,
release 0.2.2; no migration, schema 29); restarted through `START DEMO + DASHBOARD.bat` (doctor + reconcile passed)
and `START DASHBOARD.bat`. Verified after restart: engine RUNNING, account trade mode DEMO, reconciliation CLEAN,
0 positions / 0 orders, balance = equity 9,607.31 USD, symbols {BTCUSD}, XAUUSD `awaiting_market` (re-admitted
automatically when its market reopens: ASN-016 fix now live), dashboard HEALTHY with the research and readiness
endpoints, preflight READY_FOR_PAPER with the single DEMO blocker "no fresh quote for XAUUSD" (market closed).
Live two-symbol DEMO operation: **VERIFIED WITH BROKER DEMO ORDERS** (2026-09-28): XAUUSD re-admitted automatically
at 22:02 UTC on 2026-09-27 (SYMBOL_ADMITTED); since then 41 DEMO positions (BTCUSD 23, XAUUSD 18) with 6 cross-symbol
overlaps, e.g. 33 s and 45 s, combined initial risk about 0.47-0.49 % of equity (each about 0.25 %, cap 0.75 %). One
XAUUSD entry was correctly blocked by correlation (only 7 aligned samples just after the reopen); other blocks were the
re-entry rule. The operator restarted the runtime with the launcher at 19:56 GMT+4 on 2026-09-27 (same 0.2.2 code;
startup recovery CLEAN, no position open across the gap). New finding ASN-022 (false ORPHAN_BROKER_ORDER on broker
stop-loss executions): FIXED on the branch 2026-09-28 (TESTED-FAKE, 29 tests, full suite 1665 passed / 9 skipped),
Built as release 0.2.3 (`b9e779a`, smoke test PASSED), NOT DEPLOYED (the running 0.2.2 still shows the one-cycle false block; deploying needs a flat, operator-approved restart, started from the operator's own launcher).

## Current phase (2026-09-27 afternoon, branch `feature/independent-strategy-research`, release 0.2.2 (see deployment above))

Running (unchanged, not interrupted): 0.2.0 (`7f604ab`) DEMO runtime and dashboard, restarted by the operator at
03:06 GMT+4, schema 29, IC Markets DEMO account (trade mode DEMO), broker FLAT, balance = equity 9,609.01 USD,
reconciliation CLEAN, kill switch DISENGAGED (operator-set, untouched), running symbols {BTCUSD}; XAUUSD excluded
at startup (market closed) and, being 0.2.0, NOT re-admitted when its market reopens (ASN-016, fixed in 0.2.1+).

This branch (on top of 0.2.1) adds, all TESTED-FAKE, NOT DEPLOYED:
- Independent per-strategy research (`independent-research`, `research-snapshot`): one session per active
  strategy plus the selector over identical bars, separate research DB, reserved-OOS and production-DB guards,
  trial ledger, PSR/DSR/PBO, selector study, gross/net/cost loss classes. Report:
  docs/research/INDEPENDENT_STRATEGY_RESEARCH_2026-09-27.md.
- Strategy Lab: rejected proposals (filter · lost), recorded costs, gross-versus-net block, INDEPENDENT
  RESEARCH tab. Readiness panel: operator taxonomy (BLOCKED BY ... / AWAITING COMPLETED BAR / ELIGIBLE FOR A
  NATURAL SIGNAL), latest signals, last actual block, per-position link to its Strategy Lab trade.
- Browser-verified on a scratch copy (1280x720, 1366x768, 1920x1080, 2560x1440; light and dark).

Research result (development data, BACKTEST origin): every one of the 6 strategies is net-negative on its own on
BTCUSD, XAUUSD and GBPJPY; no gross R covers its cost R; the selector's expected edge does not predict realized
edge (ASN-018/019/020). DEMO: 70 attributed closed trades, net -98.84 USD (microstructure 68 trades -108.37,
statistical_reversion 2 trades +9.53); 2,362 deals reconcile exactly to the broker balance. No strategy is
profitable or promotable. No live parameter, selector or safety setting was changed.

Deployment of 0.2.2 needs operator approval (flat controlled restart of runtime + dashboard; no migration).

## Current phase (2026-09-27 early, branch `fix/multi-position-readiness`, release candidate 0.2.1)

Running: 0.2.0 (`7f604ab`), schema 29, broker FLAT (0 positions, 0 orders, balance = equity 9,642.07 USD), kill
switch DISENGAGED, reconciliation CLEAN, preflight `READY_FOR_PAPER` (DEMO blocker: XAUUSD market closed).
This branch adds per-strategy gross winners/losers and live unrealized P&L to Strategy Lab and lists each open
position with its strategy in MULTI-POSITION READINESS (TESTED-FAKE, browser-verified on a scratch copy), on
top of the ASN-016/017 fixes. Full suite 1593 passed, 9 skipped. Current DEMO evidence (backup
`pre_multi_position_deploy_20260926T223537Z`): 47 closed attributed trades, all `microstructure_acceleration`,
23W/24L, net -65.78 USD; 2,316 deals reconcile exactly to the broker balance. NOT DEPLOYED: needs a controlled
restart of the DEMO runtime and dashboard with operator approval (no migration needed).

## Phase before (2026-09-26 night, branch `fix/multi-position-readiness`)

**Supersedes the deployment note below:** release 0.2.0 (`7f604ab`) IS deployed -- the DEMO runtime and
dashboard were restarted 20:30:54 GMT+4 and migration 0029 is applied (schema 29).

Multiple simultaneous positions: the entry cycle, correlation gate, risk governor and attribution already
supported two symbols (broker deals prove a 56 s XAUUSD+BTCUSD overlap on 2026-09-25). Since Friday's close
the bot is single-symbol because XAUUSD's market is closed and GBPJPY is disabled -- expected. Fixed on this
branch (TESTED-FAKE, not deployed): ASN-016 a symbol closed at startup was never re-admitted (the running
process will NOT trade XAUUSD after Monday's open until restarted with this fix); ASN-017 working orders on
another symbol now take a max_open_positions slot. New dashboard panel MULTI-POSITION READINESS. Re-verified
2026-09-27 01:30 GMT+4: same diagnosis on 24 h of fresh evidence; added interleaved-exit attribution and
cross-symbol UNKNOWN-block tests (no new defect). Full suite 1587 passed, 9 skipped. Live two-symbol DEMO verification: PENDING (market closed). Deployment: controlled
flat restart, operator approval required.

## Current phase (2026-09-26 evening, branch `feature/strategy-lab-attribution`, release 0.2.0)

**This section supersedes the Strategy Lab paragraph below.** Strategy Lab has been rebuilt on a
read-only evidence engine (`dashboard/strategy_lab.py`, docs/STRATEGY_LAB.md). Attribution uses the
durable chain only: position → entry order → runtime chain → proposal → entry context → broker ticket.
Accounting runs over the de-duplicated broker-history + runtime deal population, with an independent
SQL reconciliation. The new Strategy Lab view has DEMO/PAPER/BACKTEST tabs, filters, detail pages,
compare-3, paginated trades with full lifecycle, unattributed and reconciliation sections, and a
review shortlist. Measured on the 11:02 UTC backup: 2,272 deals reconcile exactly (9,652.33 USD =
SQL = broker balance). 25 closed DEMO trades are attributed, all `microstructure_acceleration`
(−55.52 USD, 11W/14L). Unattributed: 1,101 EXTERNAL_EXPERT (magic 770115) and 8 UNKNOWN_SOURCE
positions.

Fixed: ASN-008 (quick_check status/health), ASN-009 (JSON model artifacts, no pickle), ASN-010
(optional pinned `terminal_path`), ASN-013 (legacy chains inflated the registry signal counts) and
ASN-014 (close orders sent with magic 0). Full suite **1,557 passed, 9 skipped**. Browser-verified
(Chromium viewport emulation, not native DPI) at 1366×768, 1600×900, 1920×1080, 2560×1440 and the
125 %/150 % equivalents: no page overflow, no chart label overlap, no JS errors.

**Not yet deployed.** The running DEMO process (started 14:49 from `bac6145`) and the live dashboard
still run the previous code. Deployment needs migration 0029 and a controlled restart; see
docs/STRATEGY_LAB.md "Safe deployment". Do not run the new code's CLI against the production
database while the old runtime is live, because it migrates automatically. Live broker at 15:00 GMT+4:
the IC Markets DEMO account, 0 positions, 0 orders. Preflight `READY_FOR_PAPER`; the only DEMO blocker is the
stale XAUUSD quote (market closed on Saturday).

## Phase before that (2026-09-26, updated ~10:37 GMT+4 after read-only re-audit)

ASN-012 has been deployed and verified: the running DEMO process (started 08:32 UTC+4 today)
loads the fix, and `reconcile` reports `CLEAN` (no unrepaired positions/orders, no UNKNOWN
resolutions). The kill switch is DISENGAGED (operator `hassan`, reason recorded). `doctor`
confirms MT5 account trade mode DEMO (ICMarketsSC-Demo), schema 28, database integrity OK,
real-money execution DISABLED. GBPJPY is excluded from `market.symbols` pending slippage
evidence (ASN-007) — this config change is present in the working tree but not yet committed.
Live engine symbols are currently `{BTCUSD}` only; XAUUSD is correctly held out on a fail-closed
stale-quote gate pending the weekend market reopen. At this audit's snapshot: zero open DEMO
positions, zero active orders.

Strategy Lab (dashboard top-level view: registry, activity, broker-verified attribution,
DEMO/PAPER/BACKTEST performance comparison, human-review shortlist) is implemented and live on
branch `dashboard/strategy-lab` (commits `f6d84ef`, `6e6824b`, `b29d8ab`; not yet pushed to
origin or merged to `main`). First real comparison since the ASN-012 restart: 24 DEMO trades
closed, all attributed to `microstructure_acceleration` on BTCUSD (11W/13L, gross -$48.33,
net -$49.95 after costs) — the other five active strategies have produced signals
(`momentum_continuation` 18, `pullback_continuation` 10, `statistical_reversion` 161) but zero
executed trades in the last 30 days; `range_breakout` and `volatility_expansion` have produced
no signals at all in that window. See `docs/STRATEGY_LAB.md` for how these figures are computed
and what UNATTRIBUTED means.

## Component status matrix (2026-09-25, Windows laptop)

| Component | Status | Evidence |
|---|---|---|
| Environment (.venv 3.13.15, pinned deps, MetaTrader5 5.0.6180) | VERIFIED ON WINDOWS | `pip check` clean; `windows_verify.ps1` PASS |
| Database (schema 28, integrity ok, backups) | VERIFIED ON WINDOWS | migrated 8 -> 28 after backup + rehearsal; data rows preserved |
| Broker server clock -> UTC (BUG #14) | VERIFIED WITH LIVE MT5 DATA | tick.time = UTC+3 measured; winter half from 212 weeks of history; startup check VERIFIED on all symbols |
| MT5 account / symbols / specs | VERIFIED WITH LIVE MT5 DATA | DEMO (ICMarketsSC-Demo), exact-name XAUUSD/GBPJPY/BTCUSD, specs captured |
| `order_check` convention (BUG #5) | VERIFIED WITH LIVE MT5 DATA | retcode 0 "Done", not sent |
| Reconciliation (read-only) | VERIFIED WITH LIVE MT5 DATA | CLEAN, 0 positions/orders |
| Strategies (6 active, 2 retired) | VERIFIED WITH LIVE MT5 DATA | `scan --source mt5` produced real signals and FLAT |
| Risk ceilings (0.25/0.75/2/5 %, 2, 1) | TESTED | config and `RiskLimits` refuse higher values (previously up to 20 %) |
| Execution costs | IMPLEMENTED (evidence-based) | XAUUSD/BTCUSD from 2,222 DEMO deals; GBPJPY slippage unknown -> BLOCK_COST |
| PAPER runtime on live data | VERIFIED WITH LIVE MT5 DATA | ~6 h + restarts, 0 cycle failures; bars processed within seconds of close; entries blocked by kill switch |
| PAPER restart correctness | VERIFIED ON WINDOWS (real-data replay) | 5,975 restarts over June 2026 XAUUSD: identical trades/equity, 0 duplicates; live restart with an open simulated position NOT VERIFIED (needs kill switch) |
| Scheduler (priority, lag) | VERIFIED WITH LIVE MT5 DATA | news fetch off-thread (<= 2 s wait); measured max lag 0.87 s |
| Dashboard (17 panels, 7 views) | VERIFIED ON WINDOWS | real Chromium at 9 viewport sizes; WebSocket + polling + reconnect; 0.05 s refresh |
| Strategy Lab (registry, activity, broker-verified attribution, DEMO/PAPER/BACKTEST performance, shortlist) | VERIFIED WITH LIVE DEMO DATA (2026-09-26) | live panels queried directly: 24 real closed DEMO trades correctly attributed to `microstructure_acceleration`, 0 UNATTRIBUTED deals; not yet merged to `main` (branch `dashboard/strategy-lab`) |
| Launchers incl. PAPER+DASHBOARD, DEMO+DASHBOARD | TESTED (static audits); PAPER/DASHBOARD run via their CLI equivalents | double-click runs of the .bat files NOT VERIFIED |
| Execution service / close / stop modification / UNKNOWN recovery | TESTED (38 chaos scenarios + runtime chaos) | NOT VERIFIED ON DEMO (no order sent) |
| Position management on a real DEMO position | NOT VERIFIED | no DEMO position has existed |
| RAG / OKF / learning observer | TESTED; running advisory-only in PAPER | OKF valid (21 concepts); 0 models (INSUFFICIENT_DATA path) |
| Historical backtests on broker history | VERIFIED WITH LIVE MT5 DATA (broker history) | backtest + walk-forward (15/15 folds hit the 5 % halt, PF 0.46-0.56) + path stress + purged CV (PSR 0.003-0.028); OOS 2026-07-01..2026-09-18 reserved, never run |
| Windows release zip (source bundle) | VERIFIED ON WINDOWS | `dist/AdaptiveScalperNext-0.1.0.zip` built from e9e599c, zip audit clean, release_smoke_test PASSED; no frozen executable |

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
idempotent migration runner — see "Schema version" below for the current
schema number and migration list (not duplicated here). `tests/test_persistence.py` (7 tests) +
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
  for millisecond-resolution tick-history dedup. The Protocol now also
  includes `order_send`/`order_check`/`positions_get`/`orders_get` — the
  execution state machine, idempotency layer, and reconciliation
  (`execution/`) that directive §118 required to exist first before these
  were exposed are built and tested (see `adaptive_scalper/execution/`
  below); `mt5_gateway.py`'s `order_send`/`order_check` are fake-tested
  thoroughly but deliberately NOT yet exercised against the real terminal
  (see the `mt5_gateway.py` entry and the "Not yet built/verified" list).
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

**Update (current state): every gate named below is now integrated.**
`BLOCK_RECONCILIATION`/`BLOCK_UNKNOWN_ORDER`/`BLOCK_DUPLICATE`/
`BLOCK_REENTRY_CHURN` were wired in by the execution-safety review round
1 fixes, and `BLOCK_PORTFOLIO_RISK` by round 2 (see those sections below
for exact detail) — this function no longer has any "always clean"
default among its composed gates. `BLOCK_MARGIN`/`BLOCK_BROKER_CONSTRAINT`
remain evaluated one step LATER, in `execution/service.py`, since they
need the EXACT broker request and a fresh `order_check()` call, which by
construction cannot happen until after this gate's `ALLOW` produces that
exact request — this is a real architectural boundary, not a gap.
`order_send` exists (`gateway/mt5_gateway.py`, `gateway/fake_gateway.py`)
and is called exclusively through `execution/service.submit_new_entry()`
for new entries (enforced by `tests/test_architecture_execution_boundary.py`)
and through `execution/close.py`/`execution/stop_modification.py` for
closes/protective-stop moves — an `ALLOW` from this gate is necessary but
not sufficient on its own; the execution layer re-runs this ENTIRE gate
fresh, twice, immediately around every real `order_send` (see
`execution/service.py`'s section below). **No real DEMO `order_send` has
ever actually been performed against a live broker** — see "Current
phase" above for why that remains a deliberate scope boundary.

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

Directive sections 29-31. This safety layer (order state machine,
idempotency, UNKNOWN resolution, reconciliation) was built and tested
FIRST, per the directive's own build order, before `order_send`/
`order_check` were added to the gateway layer at all — that historical
ordering is preserved in WORKLOG.md. `order_send` now exists and is
called exclusively through `execution/service.py` (new entries) and
`execution/close.py`/`execution/stop_modification.py` (closes/protective
stops), never directly by anything else — see those modules' sections.

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

### `adaptive_scalper/selector/` — strategy selector (IMPLEMENTED, CONNECTED, TESTED (fake), TESTED (live))

Combines the six strategies' candidate signals into one proposal, or
FLAT. Deliberately NOT "pick the highest raw confidence": `select_proposal()`
ranks qualifying candidates by cost-adjusted expected net edge
(`costs.edge.expected_gross_edge_price()` minus the symbol's
`CostEstimate.total_cost`) — a lower-confidence signal with a
substantially better risk/reward ratio can have a higher expected net
edge than a higher-confidence signal with a poor one, and a live test
below exercises exactly that case. `cost_estimates` is keyed per
`canonical_symbol` (candidates may span more than one of the three
symbols in a cycle, and cost genuinely differs by symbol). Retired
strategy keys are rejected independently (defense-in-depth, even though
`strategies.registry.StrategyRegistry` already can't produce one).
`select_and_journal_proposal()` journals every candidate's outcome —
`SIGNAL_REJECTED` (filtered out), `PROPOSAL_REJECTED` (qualified but not
the highest-edge candidate), or `PROPOSAL_CREATED` (the winner) — each
to its own chain, since every candidate is its own decision lineage.
Does NOT size money, clear the kill switch, override news/permission, or
submit orders — strictly out of scope.

`tests/test_selector.py` (13 tests): FLAT with no candidates, the
higher-net-edge-over-higher-confidence case (hand-checked EV numbers),
retired-key/low-confidence/unknown-cost/insufficient-edge rejection,
per-symbol cost lookup independence, and all three journaling outcomes.

**TESTED (live)**: walked real XAUUSD M5 bars looking for cycles where
2+ strategies fired simultaneously — found several real cases, including
one where the selector correctly returned FLAT because neither of two
real qualifying-looking candidates actually cleared the cost/edge bar
(a genuine, correct "zero trades" outcome, not a bug), and others where
it correctly picked the higher-edge real candidate between two
overlapping real signals.

### Gateway execution extension: `positions_get`/`orders_get`/`order_check`/`order_send` (IMPLEMENTED, CONNECTED, TESTED (fake), PARTIALLY TESTED (live))

Directive section 110/118: added only now that the execution safety
layer (state machine, idempotency, UNKNOWN resolution, reconciliation —
see `adaptive_scalper/execution/` above) exists to receive results
safely. New broker-independent types (`gateway/types.py`): `OrderAction`,
`OrderRequest`, `OrderSendResult`, `OrderCheckResult`, `PositionSnapshot`,
`PendingOrderSnapshot`. `Mt5Gateway._build_mt5_request()` is the ONLY
place in the codebase that constructs MetaTrader5's raw request dict;
`_order_send_result()` parses its raw response into the typed result
(zero `deal`/`order` ticket values become `None`, never a fake `"0"`
id). `FakeGateway` gained a full in-memory simulation: `order_send()`
auto-fills a DEAL into a tracked open position, applies SLTP to a
matching position, and REMOVEs a matching pending order — or, when a
test passes `order_send_responses`, returns exactly the queued canned
results instead (for reject/UNKNOWN/partial-fill scenarios).

`tests/test_mt5_request_builder.py` (15 tests, no real MT5 SDK needed —
tested against a fake stand-in module exposing the same named constants):
every `OrderAction`'s request-dict mapping, direction validation,
optional-field omission, and response parsing including the zero-ticket
→ `None` case. `tests/test_gateway_execution.py` (15 tests): the full
`FakeGateway` simulation — fills, SLTP, REMOVE (including a deliberate
ticket-mismatch case proving REMOVE only matches the exact ticket, not
"any pending order"), queued-response override, and exhaustion.

**TESTED (live), READ-ONLY ONLY**: `positions_get()`/`orders_get()`
called against the real DEMO terminal — both returned correctly (empty
lists, consistent with this account never having had an order placed
against it by anything). **`order_send()`/`order_check()` were
DELIBERATELY NOT exercised against the real terminal this session** —
doing so would place a real (if DEMO) order before the PAPER run and
full QA campaign the directive requires first (see "Current next task").
This is a conscious scope boundary, not an oversight: the code path
exists and is thoroughly tested against `FakeGateway`, but nothing in
this codebase has called real `order_send` yet.

## Live MT5 environment

The Windows laptop has an MT5 terminal logged into a DEMO account (recorded
by earlier LOCAL sessions; see WORKLOG.md). The cloud runner has no MT5 and
never connects to the laptop. `test_mt5_gateway_live.py` self-skips without
a terminal.

## Operating modes

PAPER + MT5 DEMO only; `ALLOWED_MODES == {PAPER, DEMO}` (audited: no LIVE/
REAL strings anywhere in the source). A REAL/CONTEST account is refused at
startup in both modes and re-checked (`verify_demo_before_order`) before
every broker mutation. `order_send` is reachable only through
`execution/service.py` (entries), `close.py`, `stop_modification.py`;
`order_check` additionally through the never-sending
`execution/order_check_probe.py`. No live `order_send` has been performed by
this project yet (BLOCKED-ON-LOCAL-MT5, handoff steps U-X).

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
directive §36 final-gate `BLOCK_SYMBOL_NOT_ALLOWED` check IS implemented
and composed, in `core/final_permission.py`, alongside `BLOCK_MODE`/
`BLOCK_STRATEGY_RETIRED`/`BLOCK_DATA_QUALITY`/`BLOCK_STALE_QUOTE`/the
kill-switch slice from `core/permission.py` — that module is the actual
complete final trade-permission gate `execution/service.py` consults, not
just the kill-switch slice on its own.

## Permanently retired strategies

- failed_breakout_fade
- support_resistance_reaction

Enforced independently by config validation, the strategy registry,
engine startup, the selector/final permission (`BLOCK_STRATEGY_RETIRED`),
the promotion gate, the training job (rows dropped and counted) and the
OKF validator (a retired strategy can only appear in a deprecated concept
marked `retired: true`; the advisor returns nothing for it). Tests:
`test_strategy_registry.py`, `test_final_permission.py`,
`test_learning_promotion.py`, `test_learning_jobs.py`, `test_knowledge.py`.

## Known environment/plugin issues (non-blocking)

- The `security-guidance` plugin's LLM-powered reviewer depends on a
  machine-global venv at `~/.claude/security/agent-sdk-venv` (Python
  3.14, outside this repo/git) that can independently go stale/broken
  without any change to this repository. `scripts/setup_claude_hooks.ps1`
  checks it on every run and warns if broken. Not blocking because
  `.claude/hooks/guardrails.py` (this project's own PreToolUse gate) does
  not depend on it at all.

## Execution-safety review round 2 (this checkpoint) — 3 CRITICAL fixed

A second external review of the execution layer found 8 more issues.
The 3 CRITICAL ones (explicit blockers before any real `order_send`) are
fixed this checkpoint; the 5 HIGH ones are tracked in BUG_BACKLOG.md and
being worked next.

1. **Fresh pre-send safety is now structurally enforced.**
   `execution/service.submit_new_entry()` no longer accepts a pre-built
   `FinalPermissionInput`. It now takes `fetch_fresh_evidence: Callable[[],
   FreshEvidence]` and calls it TWICE — once before `order_check`, and a
   SECOND, independent time immediately before `order_send` — re-running
   `evaluate_and_journal_final_permission()` fresh both times. A caller
   cannot satisfy the contract with a cached snapshot: the function
   itself controls when the callable runs. If anything volatile changed
   between the two calls (kill switch engaged, account left DEMO, quote
   went stale, a news window opened, reconciliation became blocking, an
   UNKNOWN/duplicate appeared, risk state moved), the second evaluation
   returns something other than `ALLOW` and `order_send` is never
   reached — new status `BLOCKED_PRESEND_RECHECK`. 9 new regression
   tests in `test_execution_service.py`, one per volatile-evidence
   scenario, each asserting `gateway.order_send_calls == []`.
2. **Authoritative MT5 retcode mapping.** New `gateway/retcodes.py`
   (`interpret_retcode()`) replaces the old "10009 or REJECTED" binary
   with real MT5 `ENUM_TRADE_RETCODE` semantics — `DONE_PARTIAL` (10010)
   maps to `PARTIAL` (real exposure, journaled with the actual filled
   volume), `PLACED` (10008) maps to `RESTING`, `TIMEOUT`/`ERROR`/
   `CONNECTION` map to `UNKNOWN` (never guessed into REJECTED or DONE,
   never blindly resent), and only retcodes with POSITIVE proof of
   rejection (REQUOTE, REJECT, INVALID_*, TRADE_DISABLED, MARKET_CLOSED,
   NO_MONEY, etc.) map to `REJECTED`. `execution/state_machine.py`'s
   `SUBMITTED` transitions widened to allow direct `PARTIAL`/`RESTING`/
   `CANCELLED` (order_send's own retcode can report these immediately,
   without an artificial intermediate `ACCEPTED` step — `SUBMITTED ->
   FILLED` directly remains illegal). `execution/close.py` uses the same
   interpreter. 16 tests in `test_gateway_retcodes.py`.
3. **Safe close now carries the same pre-send protections as a new
   entry.** `execution/close.close_position_safely()` rewritten: fresh
   `verify_demo_before_order()` + fresh `positions_get()` + fresh
   `symbol_info()`-derived filling type, exact `OrderRequest`, mandatory
   `order_check()`, then BOTH checks refreshed again independently
   immediately before `order_send`, then `interpret_retcode()` on the
   result. Deliberately does NOT check the kill switch or the full final
   permission gate — a close is risk REDUCTION and must stay available
   even when new entries are blocked; it only verifies DEMO account
   truth and accurate, freshly-proven position identity. Optional
   `conn`/`reconciliation_chain_key` params run a real reconciliation
   pass immediately after a `SENT` outcome. 14 tests including account-
   switches-mid-flight and position-disappears-mid-flight races.

Full suite: 837 passed, 0 failed, 0 skipped (up from 799).

## Execution-safety review round 2 — 5 HIGH findings also fixed

The remaining 5 HIGH findings from the same round-2 review are now also
fixed, completing all 8:

4. **Reconciliation `RECOVERED` now performs real repair.**
   `execution/reconciliation.run_reconciliation()` queries
   `history_deals_get()` for each `MISSING_LOCAL_POSITION`'s exact
   authoritative closing deal and atomically writes the real close
   price/time/volume/commission/swap/profit into local state, marks the
   position `CLOSED`, and journals `POSITION_CLOSED` — `RECOVERED` only
   appears once that repair genuinely happened. An unrepairable case
   (no closing deal found in broker history) stays `BLOCKING_MISMATCH`
   with an incident recorded, never silently relabeled or given a
   fabricated close price.
6. **`BLOCK_PORTFOLIO_RISK` implemented.** New
   `portfolio.exposure.evaluate_portfolio_risk_gate()`: simulates adding
   the proposed position to current open/pending exposure, then checks
   total/per-symbol/net-currency-direction/correlated-cluster risk
   against a `PortfolioRiskLimits`, every sub-ceiling bounded by (never
   independently higher than) the same `max_total_open_risk_pct`
   `risk.governor.evaluate_risk_gate()` enforces. Wired into
   `core/final_permission.py` between the correlation and risk gates.
7. **UNKNOWN resolution covers lost-acknowledgement sends.** New
   `execution/request_token.py`: a compact, deterministic token derived
   from `client_request_id`, embedded in every `OrderRequest.comment` by
   `execution/service.py`. New `execution.unknown
   .resolve_unknown_order_without_broker_id()`: the secondary
   correlation path for when `broker_order_id` was never recorded at
   all — matches on that token (narrowed by broker symbol) across
   current positions/pending orders/history, resolving ONLY when every
   match agrees on both state and position id; any ambiguity stays
   UNKNOWN (`conflict=True`), never guessed.
8. **Continuous position expectancy engine.** New
   `position_management/expectancy.py`
   (`evaluate_position_expectancy()`): the "if I were flat right now,
   would I still open roughly this exposure?" decision core, producing
   the `thesis_valid`/`regime_reversed` evidence `adaptive_exit
   .evaluate_adaptive_exit()` consumes — no longer a caller-fabricated
   boolean. RAG/ML advisory signals are recorded in `reasons` but
   deliberately cannot, by themselves, flip `thesis_valid` — consistent
   with RAG's and ML's own established advisory-only/observer-only
   contracts elsewhere in this codebase.

Full suite: 880 passed, 0 failed, 0 skipped (up from 837). All 8
round-2 execution-safety findings are now fixed.

## Position management runtime pieces (this checkpoint)

Building on the round-2 fixes, three more pieces from the mission's
"COMPLETE POSITION PERSISTENCE" / "PROTECTIVE STOP EXECUTION" sections
are now implemented and tested:

- **`position_management/state_store.py`** (migration `0012_position_
  management`, schema now 12): durable `position_management_state` per
  position. `initial_monetary_risk`/`entry_regime` are written ONCE at
  `get_or_create_state()` and never updated afterward (directive section
  21: moving a stop never redefines R). `peak_r` is monotonic and
  persisted — `record_review()` only ever raises it, verified to survive
  a fresh connection (process-restart equivalent). `record_exit_decision
  ()`/`record_exit_request()`/`record_exit_fill()` track the full exit
  timeline (threshold-cross/decision/request/broker-response timestamps,
  decision_r/fill_r, giveback at decision vs. at fill, expected vs.
  realized slippage). 11 tests.
- **`execution/stop_modification.py`**: the safe protective-stop
  modification service directive's "PROTECTIVE STOP EXECUTION" section
  asks for. Same two-round fresh-check pattern as `execution/close.py`
  (fresh DEMO verification + fresh position state, before `order_check`
  and again before `order_send`). Reuses `adaptive_exit
  .resolve_new_stop_price()` for the monotonic guarantee — a proposed
  stop that wouldn't improve protection sends nothing (`NO_CHANGE`).
  Refuses a stop closer to price than the broker's `trade_stops_level`
  (new `SymbolSpec` fields, populated by `Mt5Gateway`, live-verified:
  this account's IC Markets DEMO reports `trade_stops_level=0` /
  `trade_freeze_level=0` for all three canonical symbols, i.e. no
  broker-side minimum-distance restriction on this account — the code
  path is exercised by tests with a non-zero value regardless). 15 tests.
- **`position_management/manager.py`**: `review_position_once()` — the
  actual per-position review cycle, wiring `expectancy.py` →
  `adaptive_exit.py` → `state_store.py` → (`stop_modification.py` or
  `close.py`) together end to end against a real `FakeGateway` and real
  SQLite DB. 10 end-to-end tests covering HOLD, breakeven stop-advance
  (both BUY and SELL), early-take-profit/thesis-invalidation/regime-
  reversal/max-holding-time full closes, peak-R persistence across
  calls, and the R-quarantine (invalid initial risk) HOLD path.

**Update (2026-09-21 external review, fixed)**: BUG_BACKLOG.md item 8 is
resolved — `review_position_once()` now calls `state_store
.record_exit_fill()` with REAL fill_r/broker_response_at_utc computed
from the actual closing deal(s) reconciliation recovers (never a current-
quote guess), whenever that recovery genuinely confirms this position's
`broker_position_id`. `manager.py` also now: journals `POSITION_REVIEWED`
on every review and `STOP_ADVANCED` on every real stop send; only records
`request_at_utc` for outcomes that actually reached the broker
(`close.POST_SEND_STATUSES`); and routes an unprovable
`initial_monetary_risk` through a degraded-health quarantine
(`state_store.record_risk_incident()`) rather than an indistinguishable
healthy HOLD. Still not yet done: the caller loop that actually INVOKES
`review_position_once()` per open position on a real cadence (0.5-1s
directive) — this module is the per-position decision core, not the
scheduler; that's part of the still-pending full-runtime-wiring task.

Full suite: 961 passed, 0 failed, 0 skipped (up from 916 — the 2026-09-21
external-review fixes above added regression coverage across
`test_execution_stop_modification.py`, `test_position_management_manager.py`,
`test_position_management_state_store.py`, `test_execution_service.py`,
`test_portfolio_exposure.py`, `test_request_token.py`, and
`test_execution_reconciliation.py`).

## Execution-safety review round 1 fixes (prior checkpoint)

An external review of the Phase 4 execution-layer building blocks found
9 issues before controlled-DEMO execution could be considered. All nine
are fixed:

1. **Order ticket ≠ position ticket.** `Mt5Gateway._order_send_result()`
   and `FakeGateway`'s simulation both previously treated the order
   ticket as the position ticket. Fixed: `OrderSendResult
   .broker_position_id` is now ALWAYS `None` from `order_send()` (matching
   real MT5's `MqlTradeResult`, which carries no position ticket at all).
   New `execution/position_resolution.resolve_opened_position_id()`
   recovers the true position id from `history_deals_get()`/
   `history_orders_get()` afterward. `FakeGateway` now mints three
   DISTINCT tickets (order/deal/position) per fill and records a matching
   `HistoricalDeal`, so tests can no longer accidentally teach the system
   order-id == position-id. Live-verified: `symbol_info().filling_mode`
   and `positions_get()`/`orders_get()` read correctly against the real
   DEMO terminal (see below).
2. **Safe position close path.** New `execution/close.py`
   (`close_position_safely()`): freshly re-fetches `positions_get()`
   immediately before sending, refuses to send (`ALREADY_CLOSED`) if the
   position is already gone, refuses (`VOLUME_MISMATCH`) if broker
   direction/volume disagree with the caller's expectation, and always
   references the exact broker position ticket. `OrderRequest.
   position_ticket` on a DEAL now means "this is a CLOSE of that exact
   position" (`Mt5Gateway._build_mt5_request` sets MT5's `position` field;
   `FakeGateway._simulate_close_deal` mirrors it). Proven never to create
   reverse exposure under any outcome (`test_execution_close.py`).
3. **UNKNOWN resolution upgraded.** `execution/unknown.py` now resolves
   against ALL FOUR broker evidence sources (`positions_get`,
   `orders_get`, `history_orders_get`, `history_deals_get`), not just
   order history. Agreeing evidence resolves; conflicting evidence
   (`conflict=True`) is NEVER guessed away — it requires
   `PENDING_RECONCILIATION` and keeps new entries blocked; no evidence at
   all remains `UNKNOWN`.
4. **Reconciliation upgraded to real gateway truth.**
   `execution/reconciliation.run_reconciliation()` is the real
   orchestration entry point: fetches `positions_get()`, compares against
   local `OPEN` positions, reduces findings to one deterministic status
   (`CLEAN`/`BLOCKING_MISMATCH`/`RECOVERED`), records
   `execution_incidents` for blocking findings, and journals a
   `RECONCILIATION_ACTION` event every run.
5. **Final permission gate now integrates execution safety.**
   `core/final_permission.py` gained real, REQUIRED (non-optional, no
   fake-clean-default) evidence for `BLOCK_RECONCILIATION` (status !=
   CLEAN), `BLOCK_UNKNOWN_ORDER` (a dangerous unresolved UNKNOWN exists),
   `BLOCK_DUPLICATE` (an active order already exists for this proposal),
   and `BLOCK_REENTRY_CHURN` (from `position_management.re_entry
   .evaluate_reentry()`). `BLOCK_MARGIN`/`BLOCK_BROKER_CONSTRAINT` are
   evaluated one step later, in `execution/service.py`, since they need
   the EXACT broker request and a fresh `order_check()` — documented
   explicitly, not silently missing. `BLOCK_PORTFOLIO_RISK` remains a
   genuine, honestly-documented gap (no distinct cluster-heat ceiling
   beyond `BLOCK_RISK`/`BLOCK_CORRELATION` yet).
6. **Idempotency collision now fails loudly.** `execution/store
   .create_order()` raises `IdempotencyConflictError` when
   `client_request_id` repeats but any immutable field (symbol,
   direction, volume, SL, TP, broker symbol, chain_key) differs — no
   second order is created, and the stale-row-returned-silently behavior
   that could have hidden a caller bug is gone. An EXACT retry (same id,
   same fields) is still the safe, idempotent no-op it always was.
7. **Broker filling-mode constraints.** New
   `gateway/broker_constraints.derive_filling_type()` derives a
   broker-supported fill policy (IOC/FOK) from the symbol's
   `filling_mode` bitmask (new `SymbolSpec.filling_mode` field,
   populated by `Mt5Gateway`) instead of hardcoding
   `ORDER_FILLING_IOC` — returns `None` (→ `BLOCK_BROKER_CONSTRAINT`) if
   no supported policy is found. Live-verified: all three canonical
   symbols on the connected IC Markets DEMO account report
   `filling_mode=2` (IOC-only), and `derive_filling_type()` correctly
   resolves `IOC` for each.
8. **`order_check` is mandatory.** `execution/service.submit_new_entry()`
   always calls `order_check()` against the EXACT `OrderRequest` before
   `order_send()`, on the SAME unmutated request object, and journals a
   block if it fails.
9. **Fresh pre-send recheck.** `submit_new_entry()` takes a
   `FinalPermissionInput` the caller must build FRESH for every call (no
   caching) and re-runs `evaluate_and_journal_final_permission()` in full
   immediately before constructing the request — no cached ALLOW is ever
   reused.

**One execution orchestration service.** New `execution/service.py`
(`submit_new_entry()`) is now the ONLY module permitted to call
`Gateway.order_send()` for a new entry — enforced by
`tests/test_architecture_execution_boundary.py`, which scans
`strategies/`, `selector/`, `learning/`, `rag/`, `dashboard/`, and `cli/`
for direct `.order_send(` references and fails the build if found.
Journals the full lifecycle (`ORDER_SUBMITTED` → `ORDER_ACCEPTED` →
`ORDER_FILLED`/`ORDER_UNKNOWN`/`ORDER_REJECTED` → `POSITION_OPENED`).

**Live verification performed this checkpoint** (read-only only, per
CLAUDE.md — no `order_check`/`order_send` invoked against the real
terminal): connected to the real DEMO account (login number withheld per
precedent — see WORKLOG.md's "account-identifying info" note; server
`ICMarketsSC-Demo`, `trade_allowed=True`, `trade_expert=True`); confirmed
`account_info().trade_mode == DEMO`; confirmed all three canonical
symbols report `filling_mode=2` and `derive_filling_type()` resolves
`IOC` for each through the real `Mt5Gateway`; confirmed `positions_get()`
and `orders_get()` both correctly return empty lists (0 open positions,
0 pending orders) through the real gateway, matching direct
`MetaTrader5` module calls.

## Backlog fixes (Checkpoint I, 2026-09-24)

BUG_BACKLOG #7 (reconciliation symbol translation), #11 (atomic OOS
check-and-reserve), #13 (holding time to bar close; exit-timing version in
the PAPER fingerprint) and #16 (HOLD review journaling heartbeat) fixed and
tested in the cloud. Remaining open items are Windows/MT5-local or
design-level (see BUG_BACKLOG.md).

## Current next task

Cloud work is complete; the next tasks are the user's, on the laptop:

1. Review and merge PR #1 (never merged automatically).
2. LOCAL_MT5_HANDOFF.md steps A-M: install, verify, live tests, one
   never-sent `order_check`; record its retcode in BUG_BACKLOG #5 and the
   broker timestamp offset in BUG_BACKLOG #14.
3. Steps O-S: real history, backtests, reserved OOS, PAPER burn-in with a
   mid-session restart, evidence review, `[costs.SYMBOL]` from evidence.
4. Steps T-X: human kill-switch decision, controlled DEMO, first natural
   order verified manually.
5. Step Y: release build + smoke test (docs/RELEASE.md).

## Backtest / walk-forward / OOS / Monte Carlo infrastructure (directive section 80)

`adaptive_scalper/backtest/` and `adaptive_scalper/simulation/` — IMPLEMENTED, TESTED (fake, 54 tests: 16 engine, 9 walk-forward, 6 OOS, 10 Monte Carlo, 3 persistence, plus fill-model's own 10). No live/DEMO involvement anywhere in this subsystem — pure historical-bar replay.

- `simulation/fill_model.py` — `simulate_fill()` (spread+slippage-aware fill at a bar's `open`, the causal no-lookahead reference price), `round_trip_commission_price()`/`money_from_price_distance()` (share `costs.model`'s PRICE-unit conversion so simulated and live costs are directly comparable). `FillAssumptions` has no free zero defaults (matches `costs.model.estimate_cost`'s convention). Shared by both the backtest engine and the PAPER engine (see below) so fill/cost assumptions are defined once.
- `simulation/types.py` — `EvidenceOrigin` enum (`BROKER_DEMO_CONFIRMED`/`PAPER_LIVE_DATA`/`BROKER_ACCOUNT_HISTORY`/`HISTORICAL_MT5_REPLAY`/`BACKTEST`/`SIMULATED`/`IMPORTED`/`UNVERIFIED`) and `NON_LIVE_ORIGINS` — every simulated result anywhere in this codebase is labeled with which of these it is, never silently presented as live.
- `backtest/engine.py` — `run_backtest()`, the causal engine. Walks bars forward-only; a signal computed from bar `i`'s close cannot fill before bar `i+1`'s open. Reuses the SAME production decision cores the live system will use (`strategies.registry`, `regimes.classifier`, `selector.select_proposal`, `costs.model`/`costs.edge`, `position_management.expectancy`/`adaptive_exit`, `risk.governor.calculate_safe_volume`) rather than a parallel reimplementation. OHLC-only same-bar SL/TP ambiguity is resolved conservatively (stop assumed hit first). Directive section 81's historical-news limitation is honestly recorded in `BacktestResult.news_limitation_note` whenever no point-in-time `news_windows` were supplied. Named scope limit: single symbol per run (no cross-symbol portfolio-heat gating yet — depends on the multi-symbol runtime loop, still pending). Also captures the EXACT causal feature vector a strategy used to decide each entry into `SimulatedTrade.entry_features` (via `features.numeric_feature_vector()`) — real ML training input (see "Model state" below). Two modes: bounded (`force_close_at_range_end=True`, the default — a still-open trade at the final bar is force-closed so metrics are never computed over a truncated position) and incremental (`force_close_at_range_end=False` + `resume_open_position`/`resume_regime_tracker` in — for an ONGOING caller like PAPER mode that calls this repeatedly as new bars arrive; see `OpenPositionState`/`RegimeTrackerState` and the engine's own docstring for the exact resume contract a caller must uphold, and why re-passing already-processed bars is a real, dangerous bug).
- `backtest/dataset.py` — `DatasetSnapshot`/`build_dataset_snapshot()`/`compute_bars_checksum()` (SHA-256 over the exact bar sequence — content-derived, so identical data always resolves to the identical `dataset_id`), `record_dataset()`/`record_dataset_usage()`/`has_dataset_been_used_as()` against migration `0014_backtest`'s `datasets`/`dataset_usage` tables (directive section 65's dataset-integrity/usage ledger).
- `backtest/persistence.py` — `record_backtest_run()`: idempotent-on-`run_id` durable recording of a run's dataset, dataset-usage, `backtest_runs` row, and every `backtest_trades` row. `run_backtest()` itself stays pure/DB-free; persistence is an explicit opt-in a caller passes a real connection into.
- `backtest/walk_forward.py` — `run_walk_forward()`: N sequential, non-overlapping, optionally-embargoed folds, each an independent `run_backtest()` call walked forward in time (never shuffled — shuffling a time series would leak a later fold's characteristics into an earlier one's decisions). Named scope limit: each fold's feature engine warms up fresh at that fold's own start rather than reaching into a prior fold's bars, so a fold can never depend on data outside its own declared range. `embargo_bars` drops a purge gap between consecutive folds. Persists each fold as a `WALK_FORWARD_FOLD` run when given a connection.
- `backtest/oos.py` — `run_untouched_oos()`: the only sanctioned way to run genuine OOS validation. Fails closed with `DatasetContaminatedError` if the exact (content-checksummed) bar range was ever previously used for `TRAINING`/`VALIDATION`/`WALK_FORWARD_FOLD`, or already spent as `OOS` once before (repeat use defeats the point of a holdout) unless `allow_oos_reuse=True` is passed explicitly.
- `backtest/monte_carlo.py` — `run_monte_carlo()`: trade-ORDER resampling (random permutation, never resampling-with-replacement, which would fabricate outcomes that never happened) over a completed run's REALIZED P/L sequence. Deterministic given the same `seed` (a documented `random.Random(seed)` instance, no hidden global RNG state). Reports final-equity/max-drawdown distributions (mean/median/p5/p95/min/max) and probability of ruin (equity ever touching `ruin_equity_fraction * initial_equity`).

Correctness checkpoint (2026-09-24, regression suite `tests/test_backtest_correctness_regressions.py`, 41 tests): `backtest/engine.py` now (1) fills an adaptive FULL_CLOSE at the NEXT bar's open, never the decision bar's own (already-past) open, and fills the bounded range-end close at the last bar's CLOSE (`simulate_fill(..., at="close")`); (2) treats the entry bar as a full bar — its high/low are checked against the new SL/TP and it is reviewed at its close, and the regime tracker updates on it (it was previously skipped with `continue`); (3) triggers SL/TP on the executable side (bid for a long, ask for a short), fills a gapped-through stop at the open less slippage, and marks open positions at the bid/ask like MT5 `position.profit`; (4) tracks a monotonic `peak_r` from 0.0 exactly like the live `position_management_state` (previously `peak_r=current_r`, so giveback protection could never fire in simulation); (5) charges every cost exactly once — execution prices embed spread/slippage, commission and per-UTC-rollover swap are deducted at close, and `SimulatedTrade.total_cost` reports all of entry friction, exit friction, commission and swap (previously entry friction was double-deducted, and commission, swap and exit friction were never charged/reported); (6) returns `BacktestResult.pending_entry` and accepts `resume_pending_entry`, and carries a decided-but-unfilled exit as `OpenPositionState.pending_exit_reason`. `backtest/oos.py` now refuses any OOS range that OVERLAPS (any resolution, any checksum, same symbol) a range used for TRAINING/VALIDATION/WALK_FORWARD_FOLD or previously spent as OOS, via `dataset.find_overlapping_usage()` — previously only the exact checksum was checked, so a one-bar-shifted window passed.

Checkpoint A (2026-09-24, `tests/test_simulation_phase0.py`, 35 tests): a pending entry is RE-VALIDATED immediately before its next-bar-open fill (staleness vs `max_entry_fill_delay_seconds` — default two bars, so a weekend gap drops it; news window; cost and expected net edge at the fill bar's spread; safe sizing; `risk.governor.evaluate_risk_gate` with the SAME hard ceilings as DEMO incl. daily loss and drawdown; `portfolio.exposure.evaluate_portfolio_risk_gate`; `portfolio.correlation.evaluate_correlation_gate` against `external_open_positions`) and drops are listed in `BacktestResult.entry_rejections`. Daily-loss/drawdown ceilings also short-circuit scanning (`risk_halted_scans`) and survive incremental calls via `RiskState`. `BacktestConfig.risk_limits` defaults to directive section 32's values and `risk_per_trade_pct` may not exceed it. Every `SimulatedTrade` now carries `signal_time_utc`, `entry_fill_reference`/`exit_fill_reference` (`NEXT_BAR_OPEN`/`STOP_TRIGGER`/`TARGET_TRIGGER`/`RANGE_END_CLOSE`), `exit_decision_time_utc`, entry/exit spread and slippage, commission, swap, fee, `gross_pnl`, `peak_r`, `origin`, `fill_model_version` (`fill_model/v2`), `cost_provenance` (`FillAssumptions.provenance`, default `UNVERIFIED_ASSUMPTION` — unverified costs are labeled, never presented as known), `config_fingerprint` (`backtest/fingerprint.py`) and `entry_evidence` (strategy/decision/fill-revalidation evidence; ML/RAG/OKF explicitly `NOT_CONSULTED`). `walk_forward.run_walk_forward()` is labeled `SEQUENTIAL_FIXED_CONFIG_EVALUATION` (a stability check, not ML walk-forward). Monte Carlo is renamed `path_stress.run_trade_order_path_stress()` (`TRADE_ORDER_PATH_STRESS`): terminal equity is reported once, because permutation cannot change a sum; only drawdown/ruin vary. OOS overlap checks now also cover different provenance, and `allow_oos_reuse=True` is recorded as `OOS_ANALYSIS_REUSE` and spends the range like an OOS run.

Surfaced by the CLI (`backtest`, `walk-forward`, `oos`, `path-stress`, `purged-validation`) and the dashboard research panel. Not yet run on REAL bootstrapped history (BLOCKED-ON-LOCAL-MT5, handoff step O).

## PAPER mode (directive section 132)

`adaptive_scalper/paper/` — IMPLEMENTED, TESTED (fake, 13 tests: 7 state, 6 engine). No `Gateway` parameter anywhere in this package, no broker `order_send`/`order_check` — directive: "PAPER uses real MT5 market data when available. No broker order_send."

- `paper/engine.py` — `run_paper_cycle()`, the ONLY entry point. Reuses `backtest.engine.run_backtest()`'s exact production decision cores via its incremental mode (see above), adding no decision logic of its own — its whole job is correct WINDOWING (computing exactly the bar slice the resume contract needs from whatever full bar history the caller supplies, so callers never have to get this right by hand) and STATE PERSISTENCE (newly-closed trades and the resumable position/regime-tracker state are recorded atomically — `BEGIN IMMEDIATE`/`COMMIT`/`ROLLBACK` — so a crash between "decide" and "persist" is always safely retryable).
- `paper/state.py` — `paper_session_state`/`paper_trades` persistence (migration `0018_paper`), deliberately SEPARATE tables from `positions`/`orders`/`deals` (real broker evidence) — directive section 82: "Evidence classes must not silently receive identical weight." A simulated PAPER position is never reachable by `execution/reconciliation.py`'s broker-truth recovery path, and vice versa. Every recorded trade carries `origin='PAPER_LIVE_DATA'`. `record_paper_trades()` is idempotent (`INSERT OR IGNORE` keyed on `(session_key, entry_time_utc, direction)`) — a retried cycle re-deriving the SAME deterministic trades from the SAME unprocessed window is a safe no-op.
- A real, non-trivial bug was caught and fixed while proving incremental correctness: `regimes.classifier.RegimeTracker`'s hysteresis state (confirmed/candidate/candidate_count) was NOT resumable across calls — an incremental PAPER cycle restarted it from `UNKNOWN` every time, genuinely diverging from what a continuously-running tracker would decide. Fixed by adding `RegimeTracker.state`/`initial_candidate`/`initial_candidate_count` and threading a new `RegimeTrackerState` through `run_backtest()`'s resume contract and `paper/state.py`'s persistence. Proven by `tests/test_paper_engine.py::test_run_paper_cycle_incremental_feeding_matches_a_single_shot_backtest`: cycling through the same bar range in growing chunks now produces IDENTICAL final state (equity, every trade, the still-open position) to one continuous `run_backtest()` call.

- Pending state across cycles (2026-09-24): an entry selected on a cycle's last bar is persisted in `paper_session_state.pending_entry_json` (migration `0019_paper_pending_entry`) and fills at the next cycle's first new bar; a FULL_CLOSE decided on a cycle's last bar travels in `open_position_json` as `pending_exit_reason`; `peak_r` travels there too. A cycle with ONE new bar now runs (it was a no-op, so live PAPER lagged a bar), and a bar history with fewer than `feature_lookback+1` already-processed bars before the first new bar raises instead of silently skipping bars. Proven by `test_incremental_paper_cycles_match_a_single_continuous_run` (3 seeded random walks x chunk sizes 2/3/7/25, real strategies, slippage + commission): final equity, every trade, the open position and the pending entry are identical to one continuous run.

- Checkpoint A (2026-09-24): PAPER starts NOW — a new session decides only the most recent supplied bar (deep history is context, never replayed as `PAPER_LIVE_DATA`; BUG_BACKLOG #10 fixed). Sessions are bound to their configuration fingerprint; resuming under a different configuration (or a legacy session with history and no fingerprint) raises `PaperSessionConfigMismatchError` — start a new session key. Risk state (peak equity, current UTC day's realized P/L) persists across cycles. `run_paper_cycle()` accepts `external_open_positions`/`correlation_matrix` so a multi-symbol runtime applies the cross-symbol gates. One-new-bar-at-a-time cycling is proven identical to one continuous run (`chunk=1` in the property test).

Wired: `runtime/paper.py` feeds live closed bars from the one `SynchronizedGateway` every entry cycle (`paper` command / `START PAPER.bat`); the dashboard positions panel shows sessions. PAPER burn-in on real data: BLOCKED-ON-LOCAL-MT5 (handoff steps P-R).

## Research validation layer (Phase 1, 2026-09-24)

`adaptive_scalper/research/` — IMPLEMENTED, TESTED (cloud, `tests/test_research_validation.py`, 22 tests). Research-only: nothing on the execution-critical path imports it and it imports nothing that reaches a broker, the kill switch or risk sizing (both directions AST-checked).

- `splits.py` — `LabelInterval`, purging (training samples whose label interval overlaps any test sample), embargo (samples starting within `embargo_seconds` after a test block), `purged_kfold()`, `cpcv_splits()` (C(N,k) combinatorial purged CV), `cpcv_path_count()`/`cpcv_paths()`. Time-ordered only; unsorted input is refused.
- `stats.py` — `probabilistic_sharpe_ratio()`, `expected_max_sharpe()`, `deflated_sharpe_ratio()`, `probability_of_backtest_overfitting()` (CSCV). PBO reports `computable=False` (never 0) with fewer than 2 trials or an odd/too-small group count.
- `ledger.py` + migration `0021_research_trials` — append-only (triggers) trial ledger; `family_trial_count()` counts FAILED/ABANDONED trials too, and `family_sharpe_variance()` feeds DSR.
- Implemented from the published definitions rather than via the third-party `purgedcv` package (no new dependency, no license question); every function is checked against a hand-constructed case (e.g. a purge/embargo split worked by hand, E[max of 1000 N(0,1)] against a seeded simulation, PBO = 0 for a dominant config and = 1 for an always-reversing pair).
- NOT yet wired: no CLI command runs these yet (Phase 8), and the model walk-forward (Phase 6) will be their first real consumer.

## Broker chaos harness and execution recovery (Phase 2, 2026-09-24)

`tests/chaos_harness.py` — `ChaosGateway`, a deterministic `FakeGateway` with a per-method, per-call fault plan (`raise_`, `returns`, `mutate_then_default`, `default_then_raise` = the broker did it but the ack was lost, `default_then_return`) and `SimulatedCrash` (a `BaseException`, so it passes through `except Exception` like a real process death). `tests/test_broker_chaos.py` (38 tests, TESTED cloud): DEMO->REAL, disconnect, terminal/broker permission off, CLOSEONLY/SHORTONLY, identity change, stale/future/missing quote between the two pre-send rounds; `order_check` timeout/bad retcode; `order_send` timeout, lost ack after a real fill (exception and TIMEOUT retcode), UNKNOWN with a broker id resolved later from order history, ambiguous token correlation (duplicate exposure), DONE_PARTIAL, PLACED, duplicate callback; crash between proposal/check, after send, after ack before position resolution, during a close; kill switch blocks entries but never liquidates or blocks a close; close refused on REAL; close/SLTP send timeouts; position vanishing mid-close; partial closing fill; orphan broker position; vanished local position; vanished resting order; DB locked before submission; kill-switch engage under a lock.

Two real defects found and fixed (7 of the tests fail without the fixes):

- An exception from `order_send` escaped `execution/service.py`, `close.py` and `stop_modification.py`, leaving an entry order SUBMITTED with no incident — new exposure was NOT blocked although the broker may have filled it. Now: `order_check` exceptions block (nothing was sent); `order_send` exceptions are UNKNOWN (entry: order -> UNKNOWN + `UNKNOWN_OUTCOME` incident; close: UNKNOWN + immediate reconciliation), never resent.
- `execution/unknown.py`'s resolvers were never applied, and nothing quarantined orders a dead process left SUBMITTED/ACCEPTED, so an UNKNOWN either never blocked or blocked forever. New `execution/recovery.py`: `quarantine_interrupted_submissions()` (startup only) and `apply_unknown_resolutions()` — on positive broker proof only, UNKNOWN -> FILLED/PARTIAL (entry deals + local position from IN-deal evidence, strategy key recovered from the decision chain) or REJECTED/CANCELLED/EXPIRED, resolving the incident; conflicting/resting/no evidence stays blocked (-> PENDING_RECONCILIATION on conflict/resting).
- Runtime-level chaos (news provider down, RAG/ML/OKF/dashboard failures) is covered with the runtime orchestrator (Phase 3).

## Runtime orchestrator (Phase 3, 2026-09-24)

`adaptive_scalper/runtime/` — IMPLEMENTED, CONNECTED, TESTED (cloud, simulated broker: `tests/test_runtime.py` 21 tests, `tests/test_runtime_architecture.py` 8 audits). NOT yet run against a real MT5 terminal (BLOCKED-ON-LOCAL-MT5; see LOCAL_MT5_HANDOFF.md once written).

- `engine.py` — `RuntimeEngine`: one DB connection, one gateway, one thread. `startup()` in directive section 115 order, fail-closed: DB integrity; kill switch READ only (never bootstrapped/cleared — the engine runs with it UNINITIALIZED/ENGAGED and simply blocks new entries); MT5 initialize; **a REAL/CONTEST account refuses to start in PAPER and DEMO alike**; resolve + validate the three symbols (unresolvable ones excluded with a WARNING event, never substituted); retired strategies asserted absent; DEMO: `quarantine_interrupted_submissions`, reconciliation, `apply_unknown_resolutions`; news refresh; RAG ingestion + index rebuild; OKF bundle load. Scheduler tasks: `position_cycle` (P0, ~1s, DEMO), `entry_cycle` (P1, ~4s), `news_refresh` (P2, ~20m), `rag_ingest` (P3, 60s), `rag_rebuild` (P3, 10m), `heartbeat` (P4, ~1s).
- `scheduler.py` — single-threaded, priority-ordered, per-task exception isolation (a crashing scanner cannot starve position management; failures deduplicated into one TASK_FAILED event).
- `demo.py` — position cycle (reconciliation journaled only when not CLEAN, UNKNOWN resolution, per-position `review_position_once` with decision-time context from `position_entry_context`, rebuilt from the decision chain for recovered positions); entry cycle decides once per newly CLOSED bar: global short-circuit (kill switch, DEMO/terminal/broker permission, dangerous UNKNOWN, reconciliation, news outage, daily loss, drawdown) -> features -> persisted regime tracker -> six strategies -> `SIGNAL_CREATED` -> advisory evidence -> `select_and_journal_proposal` over the LIVE cost estimate (unknown configured cost -> `BLOCK_COST`) -> `calculate_safe_volume` -> `execution.service.submit_new_entry` with `fresh_evidence()` rebuilding the complete `FinalPermissionInput` from fresh broker/DB truth on both calls (read-only reconciliation, exposures incl. pending, correlation, portfolio heat, daily P/L from broker deals, persisted peak equity, re-entry check).
- `paper.py` — `run_paper_cycle` per symbol with the other symbols' simulated exposure + correlation matrix, news windows from the calendar, and a global entry block (kill switch not DISENGAGED, news outage/conflict). Zero `order_check`/`order_send` (audited). A config change halts that symbol's session with a CRITICAL event (`[runtime] paper_session_tag` starts new sessions).
- `market_data.py` — closed bars judged against the symbol's own fresh tick time (same broker clock as the bars), never this machine's UTC clock; `news_monitor.py` — slow-cadence remote refresh, local final check, UNAVAILABLE/STALE/CONFLICT fail closed; `advisory.py` — RAG / ML observer (`learning/observer.py`, STAGE 1, zero influence) / OKF evidence, journaled as `RAG_USED`/`MODEL_USED`, never passed to any gate; any failure is DEGRADED (proven: identical decisions with every advisory source raising); `state.py` + migration `0022_runtime` — `runtime_state` (heartbeat, components, why-no-trade, symbol snapshots, reconciliation, regime trackers, peak equity), deduplicated `runtime_events`, `entry_decisions`, `position_entry_context`; `logging_setup.py` — JSONL + rotating human logs with secret redaction; `gateway/factory.py` — the ONE place `Mt5Gateway()` is constructed (always `SynchronizedGateway`), CLI migrated to it.
- Config: `[runtime]` cadences (validated: position reviews at least as frequent as entry scans) and per-symbol `[costs.SYMBOL]` evidence (`None` = unknown, never zero; provenance label).
- Known limits: broker timestamp convention (server time vs UTC) is unverified and matters for quote freshness/news windows — BLOCKED-ON-LOCAL-MT5; PAPER keeps one simulated equity per symbol session; `POSITION_REVIEWED` is journaled on every review (~1/s per open position).

## RAG ingestion + OKF knowledge layer (Phases 4-5, 2026-09-24)

- `rag/ingestion.py` + migration `0023_rag_ingestion` — IMPLEMENTED, CONNECTED (engine task `rag_ingest`, P3, 60s; also at startup), TESTED (cloud: `tests/test_rag_ingestion.py`, 12 tests). Journal / paper_trades / execution_incidents / research_trials / ERROR+CRITICAL runtime_events -> all eight typed memories, each with `source_key` (idempotent: partial unique index) and `origin` (DEMO vs PAPER never pooled); per-source watermarks. The DEMO/PAPER cycles no longer write RAG on the hot path. Failure = `rag_ingest` DEGRADED, trading unaffected (tested).
- `adaptive_scalper/knowledge/` (OKF v0.2 — confirmed latest; canonical repo now `GoogleCloudPlatform/open-knowledge-format`) — IMPLEMENTED, CONNECTED (`RuntimeEngine` loads `KnowledgeAdvisor` over `knowledge/` into the evidence-only `AdvisoryPanel`; DEMO journals it as `RAG_USED` with `source: OKF`, `influence: NONE`), TESTED (cloud: `tests/test_knowledge.py`, 38 tests). Safe loader (safe_load, size cap, symlink refusal), OKF section-11 conformance, project policy (provenance, trust tiers, lifecycle/stale/supersession, evidence types stable only when human-reviewed, retired-strategy and symbol scope, control-key ban, credential + raw-record scan, quarantine), direct lookup/search, advisor, retrieval benchmark. Isolation audit: the package imports nothing that can trade/size/permit/touch the kill switch; only the engine and the operator CLI import it.
- `knowledge/` — the Git-tracked curated bundle: 21 concepts (3 architecture/engineering decisions, 6 active + 2 retired strategy definitions, 1 research finding [draft, unverified], 1 model card [draft], 2 lessons learned [draft], 4 safety procedures, 2 runbooks) + `index.md` (`okf_version: "0.2"`) + `log.md`. Zero validation errors (test-enforced); strategy definitions test-pinned to the code's strategy versions.
- Benchmark (`docs/KNOWLEDGE_MEMORY.md`): direct OKF vs TF-IDF vs SQLite FTS5/BM25 — no retrieval change justified; TF-IDF RAG index kept.

## ML training job + model walk-forward, DEMO cost evidence (Phases 6-7, 2026-09-24)

- `learning/model_walk_forward.py` — IMPLEMENTED, TESTED (cloud, `tests/test_learning_jobs.py`). Expanding-window folds that train ONLY on the past, purge label overlap (+ optional gap), out-of-fold AUC / Brier / log loss / Brier skill vs the training base rate (must beat it by >= 1%), reliability bins + ECE, subgroup metrics by strategy / regime / session with a stability check. `promotion_ready` is always False.
- `learning/jobs.py` `run_training_job()` — IMPLEMENTED, TESTED (cloud). One origin per job (BACKTEST or PAPER, never pooled); untouched-OOS runs never read; rows overlapping any reserved OOS range dropped and counted; retired-strategy rows dropped; walk-forward -> final temporal retrain -> registered BASELINE / INSUFFICIENT_DATA (never CURRENT, checked) -> append-only MODEL_WALK_FORWARD trial -> promotion gate evaluated on the true facts and REPORTED only (no state change) -> rollback evidence (previous version's skill). The Stage-1 observer then scores the BASELINE model with zero influence. CLI: `learning train`, `model-walk-forward`.
- Migration `0024_learning_and_cost_evidence`: `backtest_trades.entry_features_json` (persisted decision-time features; pre-0024 rows are excluded from training, never imputed) and `execution_cost_observations`.
- `costs/observations.py` — IMPLEMENTED, CONNECTED (DEMO entry cycle after `submit_new_entry` returns; engine task `cost_evidence_sweep`, P3, 60s), TESTED (cloud simulated broker, `tests/test_cost_observations.py`). Per DEMO order that reached the broker: quote time, bid/ask/spread, requested vs volume-weighted fill price, adverse slippage, entry commission/fee, retcode/comment, fill type, deal count, the estimate, session, ATR / realized volatility / spread percentile, news proximity; exit commission/fee and swap completed once the position closes. A failure is a WARNING event and never touches the order. `summarize_observations()` is operator evidence (>= 30 filled and closed) — it never writes config or relabels costs. TESTED-LIVE-DEMO: no (BLOCKED-ON-LOCAL-MT5; quote time is broker server time, backlog #14).

## Operator CLI + observer-only dashboard (Phases 8-9, 2026-09-24)

- `order-check-probe` (Phase 16 / handoff step L) + `execution/order_check_probe.py` + migration `0026_order_check_probes`: one never-sent DEMO `order_check` after fresh DEMO/permission/identity/quote/direction/risk/constraint verification; evidence recorded. IMPLEMENTED, CONNECTED (CLI), TESTED-FAKE (`tests/test_order_check_probe.py`); live retcode BLOCKED-ON-LOCAL-MT5.

- `adaptive_scalper/cli/` (package; replaces the single `cli.py`) — IMPLEMENTED, CONNECTED, TESTED (cloud: `tests/test_cli.py`, `tests/test_cli_commands.py`). `python -m adaptive_scalper.cli`: doctor, status, health, symbols (captures broker specs), strategies, why-no-trade, journal recent, costs observed, kill-switch status/engage/bootstrap/clear, history bootstrap/status, broker-history import/status (login masked), paper, demo, scan, analyse (`--source mt5|db`, side-effect free), reconcile, news status/refresh/upcoming, backtest, walk-forward, oos, path-stress, purged-validation, models, learning status/train/scores, model-walk-forward, rag status/stats/similar/rebuild-index/verify-index/ingest, okf status/validate/search/benchmark, dashboard. Every broker-facing command refuses a non-DEMO account; `paper`/`demo` print the safety banner, never touch the kill switch, and refuse to start while another runtime's heartbeat is fresh. `OperatorAuthority` is constructed only in `cli/operator.py` (audit updated). Broker-facing commands verified to FAIL cleanly without MetaTrader5; live behaviour is BLOCKED-ON-LOCAL-MT5.
- Migration `0025_symbol_specs` + `gateway/spec_store.py`: the broker SymbolSpec captured by `symbols`, `history bootstrap` and runtime startup, so offline research runs without a terminal (missing spec = clear error, never invented).
- `runtime/analysis.py`: side-effect-free analysis (raw regime + every strategy's would-be signal; no journal, no state, no sizing).
- Dashboard — IMPLEMENTED, CONNECTED (`dashboard` command), TESTED (cloud: `tests/test_dashboard_panels.py`, `tests/test_dashboard_health.py`; also served by uvicorn and rendered in headless Chromium with no JS errors). Observer only: READ-ONLY SQLite connection (`connect_readonly`), never connects to MT5 (the previous `dashboard` command did -- removed), GET-only endpoints, 127.0.0.1 only. 15 panels (overview, components, symbols, positions, orders incl. dangerous UNKNOWN, decisions, risk, news, events/incidents, costs, research incl. spent OOS ranges, learning, memory, knowledge, history), `/ws` live push with polling fallback, per-panel failure isolation, honest NO_DATA/UNAVAILABLE/STALE states. `websockets==17.1` pinned for uvicorn's WebSocket transport.

## Current git commit

See the latest entry in WORKLOG.md for the current commit hash — this
file is updated before each commit, so the hash is recorded there rather
than duplicated (and risking going stale) here.

## Bug backlog

See `BUG_BACKLOG.md` for non-blocking known issues.

## Schema version

26 (`0001_initial`, `0002_symbol_mapping`, `0003_symbol_validation`,
`0004_historical_data`, `0005_broker_account_history`, `0006_journal`,
`0007_news`, `0008_costs`, `0009_execution`, `0010_rag`,
`0011_learning`, `0012_position_management`,
`0013_position_risk_quarantine`, `0014_backtest`, `0015_entry_fills`,
`0016_order_magic`, `0017_incident_dedup`, `0018_paper`,
`0019_paper_pending_entry`, `0020_simulation_provenance`,
`0021_research_trials`, `0022_runtime`, `0023_rag_ingestion`,
`0024_learning_and_cost_evidence`, `0025_symbol_specs`,
`0026_order_check_probes`).

## Local RAG (advisory-only)

`adaptive_scalper/rag/` — SQLite-authoritative (`rag_memories` table,
migration `0010_rag`), CPU-friendly TF-IDF retrieval
(`scikit-learn`'s `TfidfVectorizer`/cosine similarity — added to
`requirements.txt` this checkpoint since the RAG module is the first
real consumer, per the project's own "add when the module lands"
convention). `rag/index.py`'s `RagIndex` is a derived, REBUILDABLE,
in-memory artifact — never itself authoritative, no separate on-disk
index file to go stale; `rebuild()` re-fits from the current DB state on
demand. `rag/service.py`'s `RagService` is the intended public entry
point (`record()`/`rebuild_index()`/`query_similar()`/`status()`);
nothing outside `adaptive_scalper/rag/` should import `rag.store`/
`rag.index` directly.

8 memory types: `TRADE_SETUP`, `TRADE_RESULT`, `REJECTION`,
`EXIT_DECISION`, `REENTRY_DECISION`, `EXECUTION_INCIDENT`,
`STRATEGY_CONTEXT`, `SYSTEM_EVENT`.

Structurally advisory-only, not just by convention: `RagService.record()`
and `RagService.query_similar()`'s signatures accept no `Gateway`, risk
limits, kill-switch state, or permission authority — there is no
parameter through which RAG could execute, raise risk, clear the kill
switch, change the symbol universe, reactivate a retired strategy, or
bypass final permission. Verified by
`tests/test_rag_service.py::test_record_method_takes_no_execution_capable_parameters`
(signature inspection, same pattern as `calculate_safe_volume()`'s
martingale-impossibility test) and
`test_rag_service_has_no_order_send_or_gateway_import` (AST-level import
check — none of `rag/service.py`/`rag/index.py`/`rag/store.py` may
import `gateway`/`core.kill_switch`/`risk`). Every RAG failure mode
degrades to `DEGRADED` (empty results), never an unhandled exception
that could take down a real caller — RAG was never entitled to be
treated as load-bearing.

Populated by `rag/ingestion.py` (journal -> typed memories, scheduled
`rag_ingest` task; see "RAG ingestion + OKF knowledge layer" above) —
never written from the trading hot path. Queried by the runtime's
`AdvisoryPanel` (evidence only). CLI: `rag status/stats/similar/
rebuild-index/verify-index/ingest`. Tests: `test_rag_store.py`/`test_rag_index.py`/
`test_rag_service.py`/`test_rag_ingestion.py`.

## Model state / ML self-learning (observer stage)

`adaptive_scalper/learning/` implements the OBSERVER-stage machinery
(migration `0011_learning`, schema now 11):

- `learning/lifecycle.py`: `ModelLifecycleState` (`BASELINE`/
  `CHALLENGER`/`CURRENT`/`PREVIOUS_STABLE`/`REJECTED`/`DEGRADED`/
  `ROLLED_BACK`/`INSUFFICIENT_DATA`) and its `ALLOWED_TRANSITIONS` state
  machine — mirrors `execution.state_machine`'s design exactly.
- `learning/registry.py`: persisted model registry (`register_model()`/
  `transition_model_state()`, auto-incrementing versions, full lifecycle
  history). Retired strategy keys refused at BOTH registration
  (`RetiredStrategyModelError`) AND promotion-to-`CURRENT` (re-checked
  independently in case a key is retired after a model was already
  registered for it) — directive section 8's defense-in-depth pattern.
- `learning/promotion.py`: `evaluate_promotion_gate()` — pure,
  fail-closed evaluation of every directive-named promotion requirement
  (minimum samples, causal features, temporal/purged split, walk-forward,
  untouched OOS, realistic costs, calibration, subgroup stability,
  artifact checksum, rollback availability) as REQUIRED evidence fields,
  no defaults. Does NOT itself run a walk-forward/OOS evaluation — that
  is the backtest/walk-forward subsystem's job (still pending); this is
  the deterministic decision core its verified results feed into.
- `learning/drift.py`: `apply_drift_response()` — structurally guarantees
  drift can only ever LOWER a model's influence weight, never raise it;
  proven by a property-style test across a grid of weight/severity
  combinations (`test_learning_drift.py`
  ::`test_never_raises_influence_property_across_many_inputs`).
- Structural safety verified by `test_learning_structural_safety.py`:
  no `eval`/`exec`/`compile`/`__import__` anywhere in `learning/`, no
  import of `gateway`/`core.kill_switch`/`execution`, and
  `learning.registry`'s functions carry no risk-sizing-shaped parameter.
- `learning/dataset.py`: `build_training_rows()` — assembles
  `TrainingRow`s from REAL `backtest.types.SimulatedTrade`s (never
  synthetic labels). `features/bar_features.py` gained
  `numeric_feature_vector()`/`NUMERIC_FEATURE_FIELDS` (the stationary,
  cross-time-comparable numeric subset of `FeatureSnapshot`), and
  `backtest/engine.py`'s `run_backtest()` now CAPTURES that exact
  causal snapshot at the moment of entry into `SimulatedTrade
  .entry_features`/`.entry_raw_confidence` — the feature vector a model
  trains on is the exact same one the strategy actually used to decide
  the entry, never recomputed after the fact. A trade with no captured
  features, or with ANY `None` feature value (insufficient lookback,
  degenerate spread history, etc.), is EXCLUDED from the dataset rather
  than imputed — `build_training_rows()` returns the excluded count
  alongside the rows.
- `learning/training.py`: `train_entry_outcome_model()` — fits a
  `sklearn.linear_model.LogisticRegression` (directive section 64's
  suggested CPU-friendly, auditable model family) to predict "probability
  of a positive net outcome after costs" (directive section 64). Uses a
  strictly TEMPORAL split (rows sorted by `entry_time_utc`; the latest
  `validation_fraction` fraction validates, never a random/shuffled
  split) with an optional `embargo_rows` purge gap — directive section
  65's "temporal/purged split". Refuses to train below
  `DEFAULT_MIN_TRAINING_SAMPLES=200` real closed trades (directive
  section 62: "do not hard-code an unrealistically tiny 'learning
  complete' sample") or when a split contains only one outcome class.
  Reports validation accuracy/AUC/Brier-score (calibration) honestly —
  proven on a deterministic separable synthetic task to actually learn
  real structure (validation accuracy/AUC > 0.9), not just "doesn't
  crash". `save_model_artifact()`/`load_model_artifact()` — joblib
  serialize/deserialize with a SHA-256 checksum verified BEFORE
  deserializing (the artifact is always this same codebase's own
  training output, never an externally-supplied file).
  `register_entry_model()`/`train_and_register_entry_model_from_trades()`
  register a NEW model version at `INSUFFICIENT_DATA` (not enough
  samples/single-class) or `BASELINE` (**never** `CURRENT` — see next
  paragraph) via `learning.registry.register_model()`.

**STAGE 1 MODEL OBSERVER ONLY** (directive section 61): training a model
and registering it at `BASELINE` grants it ZERO execution/selector
influence — nothing in `strategies/`, `selector/`, or
`position_management/expectancy.py` consults `learning/` yet, and this
checkpoint does not wire that. Promoting a model to `CURRENT` requires an
independent, later `learning.promotion.evaluate_promotion_gate()` pass
over a formal challenger validation (STAGE 3) — training success alone is
never sufficient, and nothing here attempts it.

NOT yet done: STAGE 2 (bounded selector influence for a validated
`CURRENT`/promoted model) and STAGE 3 (formal challenger validation
feeding `evaluate_promotion_gate()`) — both require running this training
pipeline against REAL historical bars (not just synthetic test data) via
`history/store.get_bars()` and `backtest/walk_forward.py`, and a
scheduled/CLI-triggered retraining job, none of which exist yet; also
drift MONITORING (comparing a `CURRENT` model's live predictions against
realized outcomes to actually detect the degradation `learning/drift.py`
responds to) is not wired to anything live. 71 tests
(`test_learning_lifecycle.py`, `test_learning_registry.py`,
`test_learning_promotion.py`, `test_learning_drift.py`,
`test_learning_dataset.py`, `test_learning_training.py`,
`test_learning_structural_safety.py`).

## Tests

Deep-audit final checkpoint (2026-09-26): the canonical Windows suite reports 1521 passed,
9 skipped. Release 0.1.3 passed the same suite during build and again from a clean extracted
installation. `preflight` is non-mutating and currently reports `READY_FOR_PAPER`. See
`docs/DEEP_AUDIT_REPORT.md` and the six companion audit/operations reports. No order was sent
by the audit; operator-started natural DEMO activity is recorded separately.

Latest full run: see WORKLOG.md's final entry and docs/QA_REPORT.md
(Linux cloud runner, Python 3.13; `MetaTrader5` not installable there).
The 7 skipped tests are `tests/test_mt5_gateway_live.py`, which need the
laptop's live terminal (TESTED-LIVE-DEMO only when run there). Launchers
and PowerShell scripts are verified statically (`test_launchers.py`), not
executed.

## Unverified components

- Everything marked BLOCKED-ON-LOCAL-MT5 in the matrix above.
- `Mt5Gateway.copy_rates_from_pos` — implemented, no test exercises it
  (superseded by `copy_rates_range`).
- A frozen (PyInstaller) Windows build — deferred (BUG_BACKLOG #18).
