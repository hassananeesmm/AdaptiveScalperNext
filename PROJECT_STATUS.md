# PROJECT STATUS

Update this file continuously as work happens (not retroactively), per
`CLAUDE.md` rule 10 and `MASTER_BUILD_DIRECTIVE.md` §137.

## Project

Adaptive Scalper Next

## Repository

C:\AdaptiveScalperNext

## Current phase

PHASE 1 — FOUNDATION (in progress). See directive §117 for phase
definitions.

## Completed components

- Development environment: project venv at `.venv` (Python 3.13.15,
  canonical interpreter for all project Python commands — do not use the
  global Python 3.14 install), pytest configured.
- Development safeguards:
  - `.claude/hooks/guardrails.py` — deterministic, stdlib-only PreToolUse
    hook (HARD BLOCK: writes/deletes targeting the sibling `C:\AdaptiveScalper`
    project, real-money/live-trading enablement, hardcoded secrets (in
    edit/write content and in a staged commit diff), full test-suite
    deletion, destructive ops outside the project root, Claude Code
    permission-bypass attempts; WARNING/ask: individual test
    deletion, force push, `git reset --hard`, `git clean -f`, edits to
    safety-boundary files, writes outside the project root not otherwise
    covered). Wired via `.claude/settings.json`. Covered by
    `tests/test_guardrails.py` (unit + subprocess end-to-end).
  - `.claude/claude-security-guidance.md` + `.claude/security-patterns.json`
    — project-specific guidance/patterns for the security-guidance plugin's
    LLM review and regex layer (real-money enablement, retired-strategy
    reactivation, symbol-allowlist edits, hardcoded MT5 credentials).
  - `.claude/hookify-templates/` + `scripts/setup_claude_hooks.ps1` — source
    of truth + idempotent regeneration/self-test script so the above
    survives a fresh clone and a fresh machine.
  - `.gitignore` updated for `.claude/settings.local.json` and hookify
    `*.local.*` files.
- `adaptive_scalper/config/` — hard safety constants
  (`ALLOWED_CANONICAL_SYMBOLS`, `RETIRED_STRATEGY_KEYS`, `ALLOWED_MODES`,
  directive §5/§8) plus a pydantic-validated TOML config loader
  (`load_config`) that fails startup (`ConfigError`) on anything unsafe:
  unknown symbols, a shrunk retired-strategy list, an unrecognized mode,
  out-of-bounds risk values, or `news.fail_closed = false`. Shipped default
  config at `config/default.toml`.
- `adaptive_scalper/persistence/` — SQLite connection helper (WAL,
  foreign_keys ON) and a migration runner (`migrate()`, idempotent,
  transactional per migration). First migration (`0001_initial`) creates
  `schema_migrations`, `app_state`, `configuration_audit`.
- `adaptive_scalper/core/kill_switch.py` — persistent kill switch backed by
  `app_state` (directive §37). `engage()` open to any safety component;
  `clear()` requires `actor_role="operator"` (raises `PermissionError` for
  any other role — ML/RAG/strategy/model code cannot clear it through this
  API). Every transition appended to `configuration_audit` via `history()`.
- `adaptive_scalper/core/permission.py` — **kill-switch slice only** of the
  eventual final trade-permission gate (directive §36): blocks `NEW_ENTRY`
  with `BLOCK_KILL_SWITCH` when engaged; always allows
  `POSITION_MANAGEMENT`/`RECONCILIATION`. **This is not the complete gate.**
  News, cost, correlation, portfolio, risk, account/broker validation, the
  symbol allow-list, and the retired-strategy firewall are not yet
  implemented or composed into it.
- `adaptive_scalper/gateway/` (directive §110 gateway boundary, §4 DEMO
  interlock, §6 symbol resolution):
  - `types.py` / `protocol.py` — broker-independent dataclasses and the
    `Gateway` Protocol. Deliberately excludes `order_send`/`order_check`
    (waiting on the order state machine/reconciliation to exist first —
    directive §118 "no showpiece modules").
  - `mt5_gateway.py` — real implementation; the only module allowed to
    `import MetaTrader5`. **Not yet covered by an automated test** — see
    "Unverified components".
  - `fake_gateway.py` — deterministic in-memory implementation used by
    all current gateway-layer tests.
  - `demo_gate.py` — `verify_demo_before_order()`, re-fetches fresh state
    every call (no caching), fails closed with the directive §36
    vocabulary (`BLOCK_MT5_DISCONNECTED`, `BLOCK_TERMINAL_TRADING_DISABLED`,
    `BLOCK_BROKER_TRADING_DISABLED`, `BLOCK_ACCOUNT_NOT_DEMO`). Fully
    unit-tested against `FakeGateway`.
  - `symbol_resolver.py` — exact + capped-affix alias matching, fails
    closed on no-match/ambiguous, persists to the new `symbol_mapping`
    table (migration `0002`). Fully unit-tested.
  - `tests/test_mt5_gateway_live.py` — live, skip-if-unavailable smoke
    test. Runs for real on this machine (see "Live MT5 environment"):
    verified `Mt5Gateway.account_info()`/`terminal_info()` return
    well-typed snapshots, the connected account is genuinely DEMO,
    `verify_demo_before_order()` allows against it, and — real, not
    fabricated — all three canonical symbols (XAUUSD, GBPJPY, BTCUSD)
    resolve as EXACT_MATCH against IC Markets Global's live symbol list.
    Skips cleanly (does not fail) on any machine without a live terminal.

## Implementation status

No market data ingestion, no strategies, no execution path yet. Trading
(even PAPER) cannot run. The MT5 gateway wrapper is now verified against
a real (DEMO) terminal on this machine (see above) — still unverified on
any other environment.

## Live MT5 environment (this machine only)

This development machine has a real MT5 terminal already installed and
logged in: IC Markets Global, server `ICMarketsSC-Demo`,
`trade_mode=0` (DEMO per MT5's `ENUM_ACCOUNT_TRADE_MODE`). All three
canonical symbols (XAUUSD, GBPJPY, BTCUSD) exist on this broker under
their exact canonical names — confirmed live via
`tests/test_mt5_gateway_live.py`, not assumed. This is NOT something to
assume is true on any other machine or CI — do not write non-skipping
code or tests that require it. No `order_send`/`order_check` call has
been made or is planned without explicit operator sign-off.

## Authoritative specification

MASTER_BUILD_DIRECTIVE.md

## Operating modes

PAPER + MT5 DEMO only (enforced today only at the config-validation layer;
no runtime mode gate/DEMO interlock exists yet — that is MT5-gateway work).

## Real-money trading

PROHIBITED — `ALLOWED_MODES` contains no third value, and config validation
rejects any mode outside `{PAPER, DEMO}`. No order-send code path exists at
all yet, so there is nothing to place a real-money order in the first place.

## Allowed executable canonical symbols

- XAUUSD
- GBPJPY
- BTCUSD

Enforced at config-validation layer (`MarketConfig` rejects any symbol
outside this set) AND now at broker-resolution layer
(`gateway.symbol_resolver` fails closed on no-match/ambiguous — see
"Completed components"). The independent final-gate check (directive
§36's `BLOCK_SYMBOL_NOT_ALLOWED`) is not yet implemented — no final
permission gate exists yet beyond the kill-switch slice.

## Permanently retired strategies

- failed_breakout_fade
- support_resistance_reaction

Enforced today at config-validation layer (`StrategiesConfig` rejects a
`retired` list missing either key). No strategy registry exists yet for
these to actually be excluded from.

## Known environment/plugin issues (non-blocking)

- The `security-guidance` plugin's LLM-powered reviewer depends on a
  machine-global venv at `~/.claude/security/agent-sdk-venv` (Python 3.14,
  outside this repo/git). Earlier this session `claude_agent_sdk` was
  transiently not importable there (`ModuleNotFoundError`) with a stale
  `.building` lock file present — consistent with the SessionStart
  bootstrap having been interrupted before finishing. Re-running the
  install completed it, and `import claude_agent_sdk` now succeeds; the
  plugin's regex-based PostToolUse checks were unaffected throughout (they
  don't depend on this venv) and were independently verified working via
  synthetic hook payloads. Because this venv is a machine-global resource
  not tracked by this repo, its state is NOT guaranteed to stay fixed
  across machine/session boundaries — do not report it as "fully
  operational" without re-checking
  (`~/.claude/security/agent-sdk-venv/Scripts/python.exe -c "import claude_agent_sdk"`)
  at the start of a new session. `scripts/setup_claude_hooks.ps1` includes
  this check.

## Current next task

Continue Phase 1: basic dashboard health endpoint (FastAPI, directive
§83/§108, bind 127.0.0.1 only), then start Phase 2 (five-year bar/tick
bootstrap) or Phase 3 (features/regime/strategies) groundwork, composing
`gateway` + `core.permission` toward the real final permission gate as
each dependency (news, cost, risk, etc.) lands.

## Current git commit

See the latest entry in WORKLOG.md for the current commit hash — this
file is updated before each commit, so the hash is recorded there rather
than duplicated (and risking going stale) here.

## Bug backlog

See `BUG_BACKLOG.md` for non-blocking known issues.

## Schema version

2 (`0001_initial` — `schema_migrations`, `app_state`, `configuration_audit`;
`0002_symbol_mapping` — `symbol_mapping`).

## Model state

None yet — no ML models implemented (Stage 0, directive §61).

## Tests

116 passed, 0 failed, 0 skipped (on this machine; 109 on any machine
without a live MT5 terminal, where `test_mt5_gateway_live.py` self-skips):
- `tests/test_environment.py` (1)
- `tests/test_config.py` (24)
- `tests/test_persistence.py` (7)
- `tests/test_kill_switch.py` (26)
- `tests/test_guardrails.py` (23)
- `tests/test_demo_gate.py` (12)
- `tests/test_symbol_resolver.py` (16)
- `tests/test_mt5_gateway_live.py` (7 — live-terminal-only, self-skipping)

Everything except `test_mt5_gateway_live.py` is deterministic
(FakeGateway/mocks/tmp SQLite) and portable to any machine.

## Unverified components

- Broker account history, order execution, reconciliation, position
  management — not implemented at all yet.
- `Mt5Gateway` has only been exercised read-only (`account_info`,
  `terminal_info`, `symbols_get`, `symbol_info_tick`) against ONE broker
  (IC Markets Global). Behavior against a different broker's symbol
  naming/specification quirks is unverified. `copy_rates_from_pos` (bar
  history) is implemented but not yet exercised by any test, live or
  fake.
