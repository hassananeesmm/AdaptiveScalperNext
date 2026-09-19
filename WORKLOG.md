# WORKLOG

Chronological, factual record of initialization events. Append only.

## 2026-09-19

- Repository created at `C:\AdaptiveScalperNext` (git initialized, no
  commits yet).
- Created `.gitignore`, `requirements.txt` (pytest only so far),
  `pytest.ini`, and `tests/test_environment.py`.
- Created `.venv`, installed `pytest`, ran the suite: 1 passed.
- Created `CLAUDE.md` recording the 10 permanent project rules (PAPER +
  MT5 DEMO only, no real-money trading, exactly XAUUSD/GBPJPY/BTCUSD,
  failed_breakout_fade and support_resistance_reaction permanently
  retired, no weakening safety controls, no deleting failing tests to go
  green, no secrets in Git, continuous PROJECT_STATUS.md/WORKLOG.md
  maintenance) and the scope boundary against modifying the sibling
  `C:\AdaptiveScalper` project.
- User provided the complete `MASTER_BUILD_DIRECTIVE.md` (142 sections).
  Saved verbatim as instructed.
- Created initial `PROJECT_STATUS.md` and `WORKLOG.md` bootstrap files.
- Re-read `CLAUDE.md` and `MASTER_BUILD_DIRECTIVE.md` in full at user
  request; rewrote `PROJECT_STATUS.md` to the exact fields specified by
  the user; reviewed `.gitignore` and added the missing required
  exclusions (`.env.*`, `*.key`, `*.pem`, `credentials*`, `secrets*`,
  `data/*.db`, `data/*.db-*`, `models/runtime/`).
- No trading engine code written yet.

- Diagnosed and repaired the security-guidance plugin's LLM-review Python
  resolution. Root cause: `~/.claude/security/agent-sdk-venv` (a
  machine-global venv, built against the system Python 3.14 install) had
  `claude_agent_sdk` missing (`ModuleNotFoundError`) with a stale
  `.building` lock file present, consistent with the SessionStart
  bootstrap in `ensure_agent_sdk.py` having been interrupted mid-install.
  Removed the stale lock and reran `pip install claude-agent-sdk` into the
  existing venv; the SDK was actually already fully installed by that
  point (a background SessionStart install most likely finished between
  the first failing check and the fix attempt) — `import claude_agent_sdk`
  now succeeds. Verified end-to-end with synthetic PostToolUse/Stop hook
  JSON payloads piped directly into `security_reminder_hook.py`: the
  regex-based pattern layer (unaffected by the SDK issue throughout) fires
  correctly on real vulnerability shapes (e.g. `subprocess.run(..., shell=
  True)`) and does not false-positive on unrelated content. `sg-python.sh`
  interpreter resolution itself was verified correct — it resolves to the
  system Python 3.14 after correctly skipping two broken `uv` trampoline
  stubs at `~/.local/bin/python3.13`/`python3.12`. Because the SDK venv is
  a machine-global resource outside this repo, its state is not tracked by
  git and must be re-checked at the start of future sessions (see
  PROJECT_STATUS.md "Known environment/plugin issues").
- Built the committed development-safeguard layer requested for this
  project:
  - `.claude/hooks/guardrails.py` + `.claude/hooks/run-guardrails.sh`:
    stdlib-only PreToolUse hook enforcing the requested HARD BLOCK /
    WARNING policy (see PROJECT_STATUS.md for the exact list), wired via
    `.claude/settings.json`. Deliberately independent of the
    security-guidance plugin's SDK venv so it cannot go dark the way that
    dependency did.
  - `.claude/claude-security-guidance.md` and `.claude/security-patterns.json`
    using the security-guidance plugin's own extensibility points
    (additive LLM-prompt guidance + regex patterns) for project-specific
    concerns: real-money enablement, retired-strategy reactivation,
    symbol-allowlist edits, hardcoded MT5 credentials.
  - `.claude/hookify-templates/` (canonical source copies) and
    `scripts/setup_claude_hooks.ps1` (idempotent restore + venv/deps setup
    + agent-sdk-venv health check + guardrails self-test) so the safeguards
    survive a fresh clone and a fresh machine.
  - `.gitignore`: added `.claude/settings.local.json` and hookify
    `*.local.*` exclusions.
  - Hit two real snags building this: (1) writing `tests/test_guardrails.py`
    was itself blocked by guardrails.py's own real-money-pattern check,
    because the test fixtures legitimately contain the trigger strings as
    data. Fixed by adding `CONTENT_SCAN_EXEMPT_MARKERS` /
    `is_content_scan_exempt()` so content-based regex checks (not
    path-based ones) skip `tests/`, `.claude/hooks/`, and
    `.claude/hookify-templates/`. (2) The harness's own "self-modification"
    classifier denied my first attempt to `Edit` `guardrails.py` directly
    (an agent editing the hook that governs its own permissions) — per the
    tool's own guidance this was surfaced to the user rather than routed
    around; the user applied the first version of the fix, and a
    subsequent retry of the same edit was allowed, after which the
    equivalent secret-detection-on-write check was added the same way.
- Built Phase 1 foundation code (`MASTER_BUILD_DIRECTIVE.md` §117):
  - `adaptive_scalper/config/`: `constants.py` (`ALLOWED_CANONICAL_SYMBOLS`,
    `RETIRED_STRATEGY_KEYS`, `ALLOWED_MODES` as frozensets — the single
    source of truth other modules must import rather than re-declare) and
    `loader.py` (pydantic-validated TOML config; fails closed via
    `ConfigError` on any unsafe value — symbols outside the allow-list, a
    retired-strategy list missing either permanently-retired key, a mode
    outside `{PAPER, DEMO}`, out-of-bounds risk percentages/position
    counts, `risk_per_trade_pct` exceeding `max_total_open_risk_pct`, or
    `news.fail_closed = false`). Shipped `config/default.toml`. Added
    `pydantic>=2.6` to `requirements.txt`, `pyproject.toml`.
  - `adaptive_scalper/persistence/`: `database.py` (`connect()` with WAL +
    foreign_keys pragmas; `migrate()` migration runner) and
    `migrations/0001_initial.sql` (`schema_migrations`, `app_state`,
    `configuration_audit`).
  - Root-caused and fixed a real bug in `migrate()`: it wrapped
    `conn.executescript(sql)` in a manual `BEGIN`/`COMMIT`, but
    `executescript()` issues its own implicit `COMMIT` before running,
    which silently closed that transaction, so the trailing
    `conn.execute("COMMIT")` raised `sqlite3.OperationalError: cannot
    commit - no transaction is active`. This had not been caught by the
    earlier "25 passed" run because `tests/test_persistence.py` did not
    exist yet at that point. Fixed by executing each statement
    individually (via a documented, deliberately-naive `_split_statements`
    splitter — adequate for today's plain-DDL migrations, not a general
    SQL tokenizer) inside one real transaction.
  - `adaptive_scalper/core/kill_switch.py` (directive §37): persistent
    kill switch backed by `app_state`. Built via a candidate-file +
    compile-check + test-first workflow at the user's explicit request
    (the file is safety-critical): `kill_switch_candidate.py` and
    `permission_candidate.py` were written, compile-checked, tested
    against a 26-case suite covering all 10 of the user's required
    behaviors, then promoted to `kill_switch.py` / `permission.py` and the
    candidates deleted. Final design: `engage()` has no role restriction
    (any safety component may trip it — risk governor, reconciliation, the
    engine itself); `clear()` requires `actor_role="operator"` and raises
    `PermissionError` for every other role, so ML/RAG/strategy/model code
    cannot clear it through this API regardless of what actor name it
    passes; every `engage()`/`clear()` call appends an audit row to
    `configuration_audit` via the new `history()` function.
  - `adaptive_scalper/core/permission.py`: explicitly labeled as the
    kill-switch slice only of the eventual full final-permission gate
    (directive §36) — not a claim that the gate is complete. Composes with
    `kill_switch.py`'s state to return `BLOCK_KILL_SWITCH` for `NEW_ENTRY`
    while always allowing `POSITION_MANAGEMENT`/`RECONCILIATION`.
- Test suite: 80 passed, 0 failed, 0 skipped (`tests/test_environment.py`
  1, `tests/test_config.py` 24, `tests/test_persistence.py` 6,
  `tests/test_kill_switch.py` 26, `tests/test_guardrails.py` 23).
- First Git commit of the repository created after all of the above
  (safeguards + Phase 1 foundation code together) — see PROJECT_STATUS.md
  "Current git commit" for the hash.
- Created `BUG_BACKLOG.md` per user request to track non-blocking issues
  going forward.
- Discovered this machine already has a real MT5 terminal installed and
  logged in (IC Markets Global, server `ICMarketsSC-Demo`, `trade_mode=0`
  i.e. DEMO per MT5's ENUM_ACCOUNT_TRADE_MODE) — MT5 login is therefore
  NOT a blocker here. No order-related calls (`order_send`/`order_check`)
  were made or will be made without explicit operator sign-off; only
  read-only calls (`account_info`, `terminal_info`, `symbols_get`, etc.)
  are used to build/verify the gateway.
- Built `adaptive_scalper/gateway/` (directive §110 MT5 gateway boundary,
  §4 DEMO hard interlock, §6 symbol resolution):
  - `types.py`: broker-independent dataclasses (`AccountSnapshot`,
    `TerminalSnapshot`, `SymbolSpec`, `Tick`, `Bar`) and `TradeMode`
    (mirrors MT5's `ENUM_ACCOUNT_TRADE_MODE`).
  - `protocol.py`: `Gateway` Protocol. Deliberately excludes
    `order_send`/`order_check` — those wait for the order state machine,
    idempotency and reconciliation (directive §29-31) to exist to receive
    their result safely.
  - `mt5_gateway.py`: real implementation; the only module allowed to
    `import MetaTrader5`, imported lazily so the rest of the codebase
    stays importable without the package installed.
  - `fake_gateway.py`: in-memory implementation of the same protocol for
    deterministic tests.
  - `demo_gate.py`: `verify_demo_before_order()` — re-fetches fresh
    account/terminal state on every call (no caching, so a mid-session
    switch off DEMO is caught) and fails closed to
    `BLOCK_MT5_DISCONNECTED` / `BLOCK_TERMINAL_TRADING_DISABLED` /
    `BLOCK_BROKER_TRADING_DISABLED` / `BLOCK_ACCOUNT_NOT_DEMO` using the
    directive §36 block-reason vocabulary.
  - `symbol_resolver.py`: `resolve_symbol()`/`resolve_all()` — exact
    match, then a capped-affix alias pattern (handles `XAUUSD.a`,
    `XAUUSDm`, `#XAUUSD`, etc.), fails closed to `NO_MATCH`/`AMBIGUOUS`
    (never guesses); `persist_resolution()`/`load_persisted_mapping()`
    against the new `symbol_mapping` table (migration `0002`).
  - Installed the `MetaTrader5` pip package into the project venv.
- Root-caused and fixed a second real bug in
  `adaptive_scalper/persistence/database.py`'s `_split_statements()`: its
  naive `;`-split (added in the earlier `executescript()` fix) also
  mis-split on a semicolon that appeared INSIDE a `--` SQL comment in the
  new `0002_symbol_mapping.sql` migration ("...per canonical symbol;
  re-resolution overwrites...") — `re-resolution` was left as bare,
  uncommented SQL and raised `sqlite3.OperationalError: near "re": syntax
  error`. Fixed by stripping `--` line comments before splitting. Updated
  `BUG_BACKLOG.md`'s existing entry for this splitter's known limitations.
  Also fixed two now-stale test assertions in `tests/test_persistence.py`
  that hardcoded "only migration 1 exists" (they now check against
  whatever migrations are actually discovered, so a future migration
  can't silently break them the same way).
- Pre-commit safety audit (user-requested, before any GitHub push):
  reviewed every untracked/modified file's diff for credentials/secrets.
  Found and fixed one real issue: `tests/test_demo_gate.py` had hardcoded
  the REAL MT5 account login number (`53044952`) and real server name
  observed from this machine's live terminal as a test fixture value —
  not a password/secret, but account-identifying information that should
  not be committed. Replaced with an obviously-fake placeholder
  (`90000001` / `Broker-Demo-Server`). Grepped the full working tree for
  the real login, server, account name, and terminal data-path GUID —
  none found elsewhere. Broadened `.gitignore`'s `data/*.db` entry to a
  blanket `data/` (raw market/tick data will live there and is not source
  to be committed).
- Full suite: 109 passed, 0 failed, 0 skipped.
- Committed as `7a2d840` ("Implement MT5 gateway foundation and symbol
  mapping"). Not pushed; no remote configured.
- Added `tests/test_mt5_gateway_live.py`: a live, self-skipping smoke test
  for the real `Mt5Gateway` class (the one piece of the gateway layer
  that had only been exercised via ad-hoc manual calls, not an automated
  test). Ran for real on this machine: confirmed `Mt5Gateway`'s typed
  wrappers work, the connected account is genuinely DEMO,
  `verify_demo_before_order()` allows against it, and — genuinely
  resolved, not fabricated — all three canonical symbols (XAUUSD, GBPJPY,
  BTCUSD) exist under their exact canonical names on IC Markets Global.
  Skips cleanly on any machine without a live terminal, so it cannot
  break portability elsewhere.
- Removed a duplicate/conflicting pytest config: `pyproject.toml` had its
  own `[tool.pytest.ini_options]` alongside `pytest.ini`, which pytest was
  silently ignoring in favor of `pytest.ini` while printing a warning
  every run. `pytest.ini` is CLAUDE.md's documented canonical location, so
  removed the duplicate section from `pyproject.toml` and left a comment
  explaining why.
- Full suite: 116 passed on this machine (109 + 7 live), 0 failed, 0
  skipped. On a machine without a live MT5 terminal: 109 passed, 7
  skipped (not failed).
