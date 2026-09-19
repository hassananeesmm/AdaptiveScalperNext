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

## Implementation status

No MT5 gateway, no market data, no strategies, no execution path yet.
Trading (even PAPER) cannot run.

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

Enforced today at config-validation layer (`MarketConfig` rejects any
symbol outside this set). Broker symbol resolution and the independent
final-gate check (directive §6/§36) are not yet implemented.

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

Continue Phase 1 foundation per directive §117: MT5 gateway (isolated per
§110), DEMO hard interlock (§4), three-symbol broker resolution (§6),
basic dashboard health endpoint. Cannot be verified against a real MT5
terminal/account in this environment without operator-provided MT5
login — build against mocks and label real-world state UNVERIFIED until a
live check is possible.

## Current git commit

See WORKLOG.md for the checkpoint commit hash created alongside this
status update.

## Schema version

1 (`0001_initial` — `schema_migrations`, `app_state`, `configuration_audit`).

## Model state

None yet — no ML models implemented (Stage 0, directive §61).

## Tests

80 passed, 0 failed, 0 skipped:
- `tests/test_environment.py` (1)
- `tests/test_config.py` (24)
- `tests/test_persistence.py` (6)
- `tests/test_kill_switch.py` (26)
- `tests/test_guardrails.py` (23)

## Unverified components

Everything MT5-related (gateway, DEMO interlock, symbol resolution,
account history, order execution) — no MT5 terminal/account has been
connected in this session.
