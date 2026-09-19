# PROJECT STATUS

Update this file continuously as work happens (not retroactively), per
`CLAUDE.md` rule 10 and `MASTER_BUILD_DIRECTIVE.md` §137.

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

C:\AdaptiveScalperNext

## Current phase

PHASE 1 — FOUNDATION (in progress). See directive §117 for phase
definitions. Not yet started: Phases 2-13.

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
idempotent migration runner. Schema at version 3:
`schema_migrations`, `app_state`, `configuration_audit`, `symbol_mapping`
(+ validation columns). `tests/test_persistence.py` (7 tests).

**KNOWN DEFECT** (tracked, not yet fixed): `_split_statements()` is a
naive `;`/`--`-comment-aware splitter, not a real SQL tokenizer. Fine for
today's plain-DDL migrations; would mis-split a migration containing a
string literal or trigger body with an embedded `;`. See BUG_BACKLOG.md.

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
  bare role string.
- `operator_authority.py` (IMPLEMENTED) — typed capability object.
  **Honesty note**: this is NOT cryptographic access control — Python
  cannot stop arbitrary code from constructing one. The real boundary is
  code review + which packages ever import this module (intended
  construction sites: CLI operator commands, the dashboard's eventual
  authenticated operator-action endpoint). Documented as such in the
  module docstring; future hardening (session/token verification inside
  `__init__`) is a drop-in upgrade that doesn't change any caller.
- `permission.py` — **kill-switch slice ONLY** of the eventual final
  trade-permission gate (directive §36). Composes `kill_switch.py`'s
  fail-closed state: blocks `NEW_ENTRY` with `BLOCK_KILL_SWITCH` for
  anything except `DISENGAGED`; always allows `POSITION_MANAGEMENT`/
  `RECONCILIATION`. **This is not the complete gate** — news, cost,
  correlation, portfolio, risk, account/broker validation, the symbol
  allow-list, and the retired-strategy firewall are not yet implemented
  or composed into it. There is currently NO code path that submits an
  order at all (see gateway section), so this gap has no live exposure
  yet, but it must be closed before `order_send` is ever added.

### `adaptive_scalper/gateway/` (IMPLEMENTED, CONNECTED where noted)

- `types.py` / `protocol.py` — broker-independent dataclasses and the
  `Gateway` Protocol. `SymbolSpec.trade_mode` preserves MT5's full
  5-state `ENUM_SYMBOL_TRADE_MODE` (`DISABLED`/`LONGONLY`/`SHORTONLY`/
  `CLOSEONLY`/`FULL`) rather than a flattened boolean, with
  `allows_new_long`/`allows_new_short`/`allows_any_new_exposure`/
  `allows_close` helper properties. Protocol deliberately excludes
  `order_send`/`order_check`/`positions_get`/`orders_get`/
  `history_orders_get`/`history_deals_get`/close/modify — waiting on the
  execution state machine, idempotency, and reconciliation to exist
  first (directive §118 "no showpiece modules"). **UNVERIFIED**: this is
  a real, tracked gap against the directive's full gateway interface
  list (§B in the mission brief), not an oversight — adding it before
  those consumers exist would be dead/untestable code.
- `mt5_gateway.py` — TESTED (live) for `initialize`/`account_info`/
  `terminal_info`/`symbols_get`/`symbol_info`/`symbol_info_tick` on IC
  Markets Global. `copy_rates_from_pos` (bar history) is implemented but
  UNVERIFIED (no test, live or fake, exercises it yet).
- `fake_gateway.py` — deterministic in-memory implementation backing all
  fake-based gateway tests.
- `demo_gate.py` — `verify_demo_before_order()`. TESTED (fake, 12 tests)
  + TESTED (live, part of `test_mt5_gateway_live.py`). Re-fetches fresh
  state every call; fails closed to the directive §36 vocabulary.
- `symbol_resolver.py` — exact + capped-affix alias matching. TESTED
  (fake, 20 tests) + TESTED (live: XAUUSD/GBPJPY/BTCUSD all EXACT_MATCH
  on IC Markets Global). **Fixed defect** (caught by external security
  review before this was ever pushed further): the alias regex
  previously alias-matched `XAUUSDT` (a genuinely different instrument —
  gold priced in Tether — on many brokers) to `XAUUSD`. Fixed by scoping
  case-insensitivity to just the canonical-symbol portion of the pattern
  and requiring a no-delimiter suffix to be lowercase-only (typical
  broker markers) rather than any case. Regression test:
  `test_currency_like_suffix_does_not_alias_match`.
- `symbol_validation.py` — NEW. A name match alone does not mean a
  symbol is safely executable. `validate_resolved_symbol()` re-checks,
  fresh, against the live gateway: `trade_mode != DISABLED`, sane
  contract spec (contract size/volume min·max·step/point/tick size·value
  all positive, `volume_max >= volume_min`), a live quote exists with
  `ask >= bid > 0`, and quote freshness within a configurable max age.
  Any failure fails closed with a specific reason
  (`NO_SYMBOL_INFO`/`TRADING_DISABLED`/`INVALID_CONTRACT_SPEC`/
  `NO_QUOTE`/`INVALID_QUOTE`/`STALE_QUOTE`). Persisted alongside the
  symbol_mapping row (migration `0003_symbol_validation`). TESTED (fake,
  24 tests). **UNVERIFIED live** — not yet exercised by
  `test_mt5_gateway_live.py`.
- `tests/test_mt5_gateway_live.py` — self-skipping (skips cleanly, does
  not fail, when no MT5 terminal is available). At last run: **skipped**
  (terminal not currently connected on this machine — see "Live MT5
  environment" below for when it last ran successfully).

## Live MT5 environment (this machine only, not guaranteed present)

This development machine has a real MT5 terminal (IC Markets Global,
server `ICMarketsSC-Demo`, account `trade_mode=0`/DEMO) installed and
logged in — connectivity fluctuated during this session (the terminal
app itself, not a code defect) but `test_mt5_gateway_live.py` passed all
7 tests on the most recent run, confirming: typed account/terminal
snapshots, genuine DEMO status, `verify_demo_before_order()` allowing,
all three canonical symbols EXACT_MATCH-resolving, and — new since the
last check — `symbols_get()` successfully converting `SymbolTradeMode`
for IC Markets' entire real symbol catalog (thousands of symbols) with
no `ValueError`, live-verifying Fix #5's enum conversion. Do NOT assume
a live terminal is present on any other machine or CI; re-run
`test_mt5_gateway_live.py` to check current connectivity rather than
trusting this note, which is a point-in-time snapshot.

## Implementation status

No market data ingestion, features, regime detection, strategies,
journal, news, cost model, correlation, portfolio, risk governor, full
final permission gate, execution state machine, reconciliation, position
management, adaptive exit, re-entry, RAG, ML/learning, backtesting,
dashboard (beyond a health endpoint in progress), CLI, or Windows
packaging exist yet. Trading (even PAPER) cannot run — there is no
code path that connects market data to a decision to an order attempt.

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

Enforced at THREE independent layers today: config validation
(`MarketConfig`), broker-name resolution (`symbol_resolver`, fails closed
on no-match/ambiguous), and broker-state validation (`symbol_validation`,
fails closed on disabled/invalid-contract/no-quote/stale-quote). The
directive §36 final-gate `BLOCK_SYMBOL_NOT_ALLOWED` check does not exist
yet — there is no final permission gate beyond the kill-switch slice.

## Permanently retired strategies

- failed_breakout_fade
- support_resistance_reaction

Enforced at config-validation layer only (`StrategiesConfig` rejects a
`retired` list missing either key). **No strategy registry exists yet**
— there is nothing yet for these to be excluded FROM at runtime; the
directive §121 retired-strategy tests (registry/signal/rank/train/
promote/execute) cannot be written until strategies exist.

## Known environment/plugin issues (non-blocking)

- The `security-guidance` plugin's LLM-powered reviewer depends on a
  machine-global venv at `~/.claude/security/agent-sdk-venv` (Python
  3.14, outside this repo/git). Verified working earlier this session
  after a transient stale-lock issue; state not guaranteed to persist
  across machine/session boundaries — re-check
  (`~/.claude/security/agent-sdk-venv/Scripts/python.exe -c "import claude_agent_sdk"`)
  before relying on it. `scripts/setup_claude_hooks.ps1` includes this
  check.

### `adaptive_scalper/dashboard/` (IMPLEMENTED, CONNECTED, TESTED (fake))

FastAPI app (`create_app(db_path, gateway=None)`) with one endpoint,
`GET /api/health`, composing `health.compute_health()` — the directive
§107 HEALTHY/DEGRADED/NEW_ENTRIES_BLOCKED/TRADING_BLOCKED/CRITICAL state
from database integrity + kill-switch status (correctly reports
TRADING_BLOCKED for a fresh UNINITIALIZED kill switch, not HEALTHY —
consistent with the fail-closed design) + optional gateway connection
state. Not yet started: WebSocket push, any panel beyond health, binding
to 127.0.0.1 by an actual run script (the FastAPI app itself doesn't
bind — that's `uvicorn.run(app, host=DEFAULT_HOST, ...)`, not yet wired
into a CLI command). `tests/test_dashboard_health.py` (9 tests).

**Fixed defect found by its own test before any commit**: `create_app()`
originally took a live `sqlite3.Connection`, which crashed under
FastAPI's worker-thread dispatch (`sqlite3.ProgrammingError`: connections
are thread-affine). Now takes a DB path and opens a per-request
connection. See BUG_BACKLOG.md.

## Current next task

Wire a `dashboard` CLI/launcher command that actually calls
`uvicorn.run()` bound to 127.0.0.1 (no CLI exists yet at all — directive
§108). Then continue toward Phase 2/3 per directive dependency order:
historical bootstrap, then features/regime/strategies, building the
composed final permission gate incrementally as each dependency (news,
cost, risk, etc.) lands. This is a genuinely large remaining scope — see
BUG_BACKLOG.md and this file's per-component notes for exactly what is
and isn't done; do not infer completion of anything not explicitly
marked IMPLEMENTED/CONNECTED/TESTED above.

## Current git commit

See the latest entry in WORKLOG.md for the current commit hash — this
file is updated before each commit, so the hash is recorded there rather
than duplicated (and risking going stale) here.

## Bug backlog

See `BUG_BACKLOG.md` for non-blocking known issues.

## Schema version

3 (`0001_initial`, `0002_symbol_mapping`, `0003_symbol_validation`).

## Model state

None yet — no ML models implemented (Stage 0, directive §61).

## Tests

161 passed, 0 failed, 0 skipped (live MT5 terminal is currently connected
— see "Live MT5 environment"; on a machine/moment without one,
`test_mt5_gateway_live.py`'s 7 tests self-skip instead of failing):
- `tests/test_environment.py` (1)
- `tests/test_config.py` (24)
- `tests/test_persistence.py` (7)
- `tests/test_kill_switch.py` (36)
- `tests/test_guardrails.py` (23)
- `tests/test_demo_gate.py` (12)
- `tests/test_symbol_resolver.py` (20)
- `tests/test_symbol_validation.py` (24)
- `tests/test_dashboard_health.py` (9)
- `tests/test_mt5_gateway_live.py` (7 — live-terminal-only, self-skipping)

## Unverified components

- `Mt5Gateway.copy_rates_from_pos` (bar history) — implemented, no test yet.
- `symbol_validation.py` against a real live broker (only fake-tested so far).
- Everything listed in "Implementation status" as not yet existing.
