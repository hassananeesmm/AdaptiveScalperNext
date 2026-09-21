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

- Discovered `origin` (https://github.com/hassananeesmm/AdaptiveScalperNext.git,
  branch `main`) already configured as the git remote and already at
  93b16b1 (matching local HEAD at the time) — pushed by the user/external
  process outside this session, not by an action taken here.

- Received an external architecture/security review (via the
  security-guidance plugin's async commit-review hook) flagging a
  parser-differential in `gateway/symbol_resolver.py`, and a separate,
  more extensive external review of the whole architecture. Addressed
  both in this increment — see BUG_BACKLOG.md's "Fixed" section for full
  detail on each. Summary:
  1. **Kill switch now fails closed on unknown state.** Replaced the
     boolean `engaged` field with `KillSwitchStatus`
     (`UNINITIALIZED`/`INVALID`/`ENGAGED`/`DISENGAGED`); a missing or
     corrupted `app_state` row no longer reads as "safe to trade". Added
     `bootstrap()` for the one-time, operator-authorized transition out
     of `UNINITIALIZED`.
  2. **Kill switch state + audit write are now atomic** (`BEGIN`/two
     inserts/`COMMIT`, `ROLLBACK` on failure) instead of two independent
     autocommit statements.
  3. **`clear()`/`bootstrap()` now require `OperatorAuthority`**, a typed
     capability object (`adaptive_scalper/core/operator_authority.py`),
     not a bare `actor_role: str`. Documented honestly: this is a code
     review/import-boundary convention, not cryptographic access control
     — Python can't prevent arbitrary construction. Real strengthening
     (session/token checks) is a documented future drop-in upgrade.
  4. **Symbol resolution now validates broker state, not just the
     name**: new `gateway/symbol_validation.py` re-checks trade mode,
     contract-spec sanity, and a live fresh quote, failing closed on any
     doubt. Persisted alongside the mapping (migration
     `0003_symbol_validation`).
  5. **`SymbolSpec` now preserves MT5's full 5-state symbol trade mode**
     (`SymbolTradeMode`: `DISABLED`/`LONGONLY`/`SHORTONLY`/`CLOSEONLY`/
     `FULL`) instead of a flattened boolean, with
     `allows_new_long`/`allows_new_short`/`allows_close` helpers so a
     future order-validation layer can correctly treat `CLOSE_ONLY` as
     "may reduce risk, may not open new exposure" rather than either
     fully-allowed or fully-blocked.
  6. **Pinned canonical Python to 3.13** in `pyproject.toml`
     (`requires-python = ">=3.13,<3.14"`) and tightened
     `tests/test_environment.py` to assert the exact 3.13.x range rather
     than merely `>= 3.11`.
  7. **Pinned exact dependency versions** in `requirements.txt` (were
     `>=` minimums) for fresh-clone reproducibility.
  8. **Rewrote `PROJECT_STATUS.md`** with an explicit
     IMPLEMENTED/CONNECTED/TESTED (fake)/TESTED (live)/UNVERIFIED/KNOWN
     DEFECT tag on every component, removing prior stale-sounding
     language (e.g. a prior version's Phase-1 "next task" line still
     said to build the DEMO interlock/resolver after they already
     existed and were tested).
  9. `BUG_BACKLOG.md`'s existing entry for the migration splitter's
     "not a real tokenizer" limitation left open and unchanged (still
     accurate — the "Fixed" item this pass was the specific
     comment-semicolon manifestation of it, tracked separately).
- Test suite after all of the above: 145 passed, 0 failed, 7 skipped (no
  live MT5 terminal currently connected — see PROJECT_STATUS.md).
- Committed as `99550a9` ("Fix 9 external-review architecture findings
  (kill switch, symbol resolution)"). Pushed to `origin/main`
  (93b16b1..99550a9) — `origin` was already configured pointing at
  https://github.com/hassananeesmm/AdaptiveScalperNext.git, branch
  `main`, from outside this session.
- Completed the Phase 1 dashboard health endpoint
  (`adaptive_scalper/dashboard/`): `health.py` (`compute_health()`,
  directive §107 states) + `app.py` (`create_app()`, one FastAPI
  endpoint `GET /api/health`). Updated `health.py` to use the redesigned
  kill switch's `blocks_new_entries`/`status` instead of the old
  `engaged` boolean it was originally written against (this module was
  mid-write when the kill-switch redesign landed). This also fixed a
  latent correctness gap: a fresh, never-bootstrapped kill switch now
  correctly reports dashboard state `TRADING_BLOCKED` rather than
  `HEALTHY`, consistent with the fail-closed design.
- `tests/test_dashboard_health.py`'s own endpoint test immediately caught
  a real bug before any commit: `create_app()` originally captured a
  live `sqlite3.Connection`, which crashed under FastAPI's worker-thread
  request dispatch (`sqlite3.ProgrammingError: SQLite objects created in
  a thread can only be used in that same thread`). Fixed by having
  `create_app()` take a DB path and open a short-lived connection per
  request instead. Documented in BUG_BACKLOG.md, plus a related
  LOW-severity backlog note: `Mt5Gateway`'s underlying SDK calls aren't
  documented as thread-safe either, and the dashboard doesn't yet guard
  against concurrent gateway calls from multiple worker threads — not a
  problem today (one read-only call per request), tracked for before the
  dashboard grows concurrent panels.
- Full suite: 154 passed, 0 failed, 7 skipped (live MT5 test still
  self-skips; terminal not connected on this machine right now).
- Committed as `332f959` ("Complete Phase 1 dashboard health endpoint").
  Pushed to `origin/main` (99550a9..332f959). MT5 terminal reconnected
  during this run — a full suite run right after showed 161/161 passed
  (0 skipped), including `symbols_get()` successfully converting
  `SymbolTradeMode` across IC Markets' entire real symbol catalog with
  no error — live-verifying the earlier trade-mode-enum fix. Updated
  PROJECT_STATUS.md's "Live MT5 environment" note accordingly, with the
  explicit caveat that it's a point-in-time snapshot to re-check, not a
  standing guarantee.
- Built `adaptive_scalper/cli.py` (directive §108): `doctor`, `status`,
  `health` (exits non-zero when not HEALTHY), `symbols`, `kill-switch
  status/engage/clear`, `dashboard` (runs uvicorn bound to 127.0.0.1).
  Only commands backed by an existing, real subsystem — no placeholder
  stubs. `tests/test_cli.py` (8 tests, deterministic: tmp config + tmp
  SQLite DB for everything except MT5-touching `doctor`/`symbols`
  behavior). Manually smoke-tested `doctor` and `symbols` against the
  live terminal: both succeeded (`mt5: reachable`; all three canonical
  symbols resolved EXACT_MATCH). Not yet folded into an automated
  skip-if-unavailable pytest case alongside `test_mt5_gateway_live.py` —
  small tracked follow-up, not a defect. Cleaned up the `data/` runtime
  DB the manual smoke test created (gitignored, never staged).
- Full suite: 169 passed, 0 failed, 0 skipped.

- Repaired (by the user, outside this repo) a Git Bash `python3` shim
  issue that had been causing `.claude` plugin hooks to fail with
  `/usr/bin/bash: line 1: python3: command not found`. Verified the fix
  at the start of this session with a harmless Bash tool call and a
  triggered PreToolUse hook (`run-guardrails.sh`) — no error surfaced.
  Confirmed this project's own guardrails hook was never actually
  dependent on the shim (it prefers `.venv/Scripts/python.exe` first);
  the affected hook must have been a different plugin's (e.g.
  security-guidance's `sg-python.sh`). No code change required here;
  resumed the full build per the user's instruction.
- Built the 5-year MT5 historical bootstrap
  (`adaptive_scalper/history/`, directive sections 46-50) — the "Current
  next task" carried over from the previous session:
  - Extended the gateway layer: `Gateway.copy_rates_range`/
    `copy_ticks_range` (protocol + `Mt5Gateway` + `FakeGateway`), and
    added `Tick.time_msc` (default 0, backward compatible) since tick
    history dedup needs millisecond resolution the existing whole-second
    `time` field can't provide.
  - `migrations/0004_historical_data.sql`: `bars`, `ticks` (both with
    UNIQUE constraints that make re-import idempotent),
    `historical_import_jobs` (resumable checkpoints — `resolution = ''`
    rather than NULL for TICK jobs, since SQLite UNIQUE treats every NULL
    as distinct and would have allowed duplicate TICK job rows),
    `historical_bar_coverage`, `historical_tick_coverage`.
  - `history/resolutions.py`, `store.py` (idempotent
    `INSERT OR IGNORE`-based inserts), `jobs.py` (checkpoint CRUD;
    `get_or_create_job` extends `requested_end_utc` forward for
    incremental resync, re-opening a completed job, but rejects widening
    `requested_start_utc` backward — tracked as BUG_BACKLOG.md #3 rather
    than silently mishandled), `coverage.py` (earliest/latest/count +
    deliberately naive gap counting — weekends/session closures are
    expected to show up as "gaps" per directive section 46, not treated
    as a defect), `bootstrap.py` (chunked resumable orchestration).
  - Explicit, documented scope decision per directive section 48's
    "document exact decision": bars target a full 5 years across
    M1/M2/M3/M5/M15; ticks default to a 30-day rolling window, not 5
    years — full 5-year raw tick history for XAUUSD/GBPJPY/BTCUSD would
    plausibly reach hundreds of millions of rows, which the directive
    explicitly permits scoping down rather than pretending was obtained.
    Both bounds are caller-configurable (CLI flags), not hardcoded.
  - Truncation safety: each chunk's cursor advances only past the last
    bar/tick actually received, not blindly to the chunk's requested
    end — so a broker response-size cap mid-chunk gets picked up on the
    next iteration instead of silently dropping data. A zero-progress
    chunk raises `RuntimeError` (persisted to the job's `last_error`)
    rather than looping forever.
  - CLI: `history bootstrap` (best-effort per symbol/resolution — one
    symbol's broker-resolution failure or mid-download error is reported
    in the JSON output and does not abort the others) and `history
    status` (coverage summary per configured symbol).
  - `tests/test_history_bootstrap.py` (13 tests, fake-gateway only):
    storage idempotency; single- and multi-chunk completion; a
    resumed-after-simulated-failure case that asserts the resume's first
    gateway call starts exactly at the persisted checkpoint (via a
    `_FlakyGateway` test wrapper that fails after N calls); a
    same-process-restart variant using a genuinely fresh
    `sqlite3.Connection` to the same database file; a completed job
    rerun making zero further gateway calls; `get_or_create_job`'s
    forward-extend/backward-reject behavior; a constructed-gap coverage
    test; `bootstrap_all` only touching symbols present in its
    canonical-to-broker map. Plus 2 new `tests/test_cli.py` cases for
    `history status` (empty state, and after a fake-driven bootstrap).
  - Added BUG_BACKLOG.md items #3 (backward-widening a job's start is
    unsupported by design, not a defect) and #4 (tick dedup relies on
    `time_msc`; flagged for re-check if a live run ever undercounts).
  - **UNVERIFIED live**: `copy_rates_range`/`copy_ticks_range` and the
    `history bootstrap` CLI command have not yet been run against the
    real IC Markets DEMO terminal — only fake-gateway-tested this
    session. That live run is the natural next step, not yet done.
- Full suite: 184 passed (169 + 15 new), 0 failed, 0 skipped (live MT5
  terminal still connected on this machine this session).

## 2026-09-19 (continued — new session)

- Resumed from a fresh session per user instruction. Verified local
  working tree had substantial uncommitted work beyond the last GitHub
  push (`7e43483`): the entire historical-bootstrap + broker-account-
  history implementation from the prior session. Ran the full suite
  (191 passed), reviewed the diff for secrets, found and fixed one real
  issue before committing — `PROJECT_STATUS.md` had picked up the real
  DEMO account login number (`53044952`) in a "TESTED (live)" note,
  account-identifying info not yet in git history. Replaced with a
  pointer to the existing precedent instead of the raw number. Committed
  as `16ff74c` ("Implement Phase 2 historical bootstrap and broker
  account history import") and pushed to `origin/main`.
- Fixed 5 points from a further external architecture review, all in
  `adaptive_scalper/gateway/symbol_validation.py` and a new
  `synchronized_gateway.py`:
  1. **Canonical asset identity** — name resolution alone isn't proof of
     identity. Added `EXPECTED_IDENTITY`, a per-symbol spec checked
     against live broker metadata. First draft required exact
     `currency_base` match for all three symbols (e.g. BTCUSD must report
     `currency_base="BTC"`); live-verified against the real IC Markets
     DEMO terminal BEFORE committing and found this would have
     permanently failed closed on BTCUSD, which this broker reports as
     `currency_base=currency_profit=currency_margin="USD"` (crypto CFDs
     settled/margined entirely in USD, base currency field unused for the
     asset name — a real, broker-specific convention, not a bug).
     Redesigned: `base_currency` is checked only for true FX/metal pairs
     (XAUUSD, GBPJPY) where it's reliable; BTCUSD's identity instead rests
     on `profit_currency="USD"` plus the broker's `description` field
     containing "bitcoin"/"btc". Re-verified live after the fix: all
     three canonical symbols now validate `VALID`. This is exactly the
     kind of thing that would have silently broken production had it not
     been checked against real broker data before commit.
  2. **Execution-grade quote freshness** — new `validate_execution_quote()`,
     deliberately separate from and stricter than the existing bootstrap-
     time tick check (which intentionally tolerates a missing/zero
     timestamp). Missing quote, missing/zero/unusable timestamp,
     implausibly-future timestamp, stale (5s default vs. 30s), or invalid
     bid/ask all BLOCK. Not yet wired into a live order path (none exists
     yet) — built as the pure, testable function the eventual final
     permission gate will call immediately before `order_send`.
  3. **Directional symbol trade mode** — new
     `validate_direction_for_new_exposure()`: DISABLED/CLOSEONLY block
     both directions for NEW exposure; LONGONLY/SHORTONLY restrict to one
     direction; FULL allows either. Uses `SymbolTradeMode`'s existing
     `allows_new_long`/`allows_new_short` helpers.
  4. **MT5 concurrency** — new `gateway/synchronized_gateway.py`:
     `SynchronizedGateway` wraps any `Gateway` and serializes every call
     through one `threading.RLock`. `cli.py`'s `dashboard` command now
     constructs a real `Mt5Gateway`, wraps it, and injects it into the
     dashboard (previously the dashboard command passed no gateway at
     all, so `mt5_connected` always read `null`). Tested with a genuine
     multi-thread concurrency probe, not just delegation — plus a
     companion "unsynchronized baseline" test proving the probe can
     actually detect a missing lock (it showed real overlap when the
     `SynchronizedGateway` wrapper was removed), so the passing case is
     meaningful evidence rather than a probe that always reports success.
  5. **PROJECT_STATUS.md staleness** — full rewrite. Removed a
     contradictory "Implementation status" paragraph that still said "no
     market data ingestion... exists yet" directly below a section
     documenting the (already complete) historical bootstrap. Fixed two
     more stale test counts caught while rewriting:
     `test_symbol_resolver.py` was documented as "20 tests" (actually 18)
     and a first-draft rewrite guessed "69 tests" for
     `test_symbol_validation.py` before actually counting (it's 48) —
     both corrected by running `pytest --collect-only` per file rather
     than estimating.
- Full suite: 218 passed, 0 failed, 0 skipped (24 new symbol_validation
  tests + 3 new synchronized_gateway tests on top of the 191 from the
  Phase 2 commit).
- Committed as `4f7c762` and pushed to `origin/main`.
- Started Phase 3 (CORE TRADING): built `adaptive_scalper/features/
  bar_features.py`, the causal feature engine (directive section 11).
  `compute_bar_features()` treats the last element of its input bar list
  as "now" and only reads backward from there — the list itself is the
  causal boundary, locked in by a no-lookahead regression test (mutating
  bars beyond a computed prefix cannot change that prefix's already-
  computed snapshot). Implements returns/log-returns, realized
  volatility, ATR/normalized range, momentum, velocity/acceleration,
  Kaufman efficiency ratio, directional persistence, range-expansion
  ratio, candle body/wick ratios, recent high/low, spread + spread
  percentile, movement-to-cost (needs an optional point_size param —
  stays `None` without it rather than mixing MT5 "points" and price
  units incorrectly), and session/hour/weekday tagging. Explicitly named
  (not silently skipped) gaps: swing/support-resistance structure,
  tick-frequency features, and cross-symbol correlation (belongs to the
  not-yet-built portfolio module). `compute_multi_resolution_features()`
  composes one snapshot per resolution.
  `tests/test_bar_features.py` (23 tests): validation, insufficient-data
  → honest `None` fields, the no-lookahead regression test, and
  hand-checked formula correctness — e.g. efficiency ratio = 1.0 exactly
  for a constructed perfect trend and < 0.3 for a choppy alternating
  series; body+upper_wick+lower_wick ratios proven to sum to exactly
  1.0 (this holds algebraically for any bar, not just the test fixture —
  verified both by direct calculation and by the general test).
- **TESTED (live)**: ran `compute_bar_features` against the real
  100,000-row XAUUSD M1 bar history bootstrapped earlier this session —
  completed instantly, every field populated with sane values (e.g.
  `spread_current=40.0` matching the widened Friday-close spread observed
  directly against the live terminal earlier), no crash on the full real
  dataset.
- Full suite: 241 passed, 0 failed, 0 skipped.
- Committed as `a569d5e` and pushed to `origin/main`.
- Built `adaptive_scalper/regimes/classifier.py` (directive section 13):
  `classify_regime()` is a pure, stateless function of one
  `FeatureSnapshot` -> TRENDING_UP/TRENDING_DOWN/RANGE/COMPRESSION/
  VOLATILITY_EXPANSION/BREAKOUT/ERRATIC/UNKNOWN with a heuristic
  confidence and reason string; missing underlying feature data resolves
  to UNKNOWN rather than guessing. `RegimeTracker` adds the hysteresis
  the directive requires ("do not close a position solely because one
  noisy observation briefly flips regime") — its `confirmed_regime` only
  changes after `min_confirmations` consecutive raw classifications
  agree on the same new regime.
  `tests/test_regime_classifier.py` (22 tests): every branch of the
  classification decision tree, confidence always in [0,1], and the
  tracker's hysteresis behavior including a candidate-streak-reset case
  (a different candidate interrupting a streak must restart the count,
  not carry over) and a confirmed-regime-reoccurring case (a raw
  observation matching the currently-confirmed regime again must reset
  any in-progress candidate streak for a different regime).
- **TESTED (live)**: walked causally through the last ~2000 real XAUUSD
  M5 bars (from the earlier bootstrap) computing features + tracked
  regime at each step, one bar at a time using only bars up to that
  point (respecting the feature engine's no-lookahead contract) — no
  crash; the resulting confirmed-regime distribution (RANGE dominant,
  with real COMPRESSION/TRENDING/ERRATIC periods) matches the intuitive
  expectation for a short-timeframe market.
- Full suite: 263 passed, 0 failed, 0 skipped.
- Committed as `a7618a6` and pushed to `origin/main`.
- Built `adaptive_scalper/strategies/` — the six directive-mandated
  active strategy families plus the strategy-registry retirement
  firewall (directive sections 9, 90, 121):
  - `base.py`: `Strategy` protocol + `StrategySignal`, which structurally
    has NO monetary/volume field (only price DISTANCES) — directive
    section 9's "a strategy must never decide money risk/volume" is
    enforced by the type itself, not just documentation. Validates
    direction/confidence/distance bounds in `__post_init__`.
  - `registry.py`: `StrategyRegistry.register()` checks every key
    against `RETIRED_STRATEGY_KEYS` unconditionally on every call and
    raises `RetiredStrategyError` — no config flag or restore path can
    bypass it, because there's no state the check depends on other than
    the hardcoded constant itself.
  - Six strategies, each self-contained and regime-gated:
    `momentum_continuation`, `pullback_continuation` (pullback within an
    intact trend, deliberately distinct entry logic from pure
    continuation per directive section 18), `range_breakout`,
    `statistical_reversion` (fades proximity to the recent range
    extreme), `volatility_expansion` (candle wick-rejection direction),
    `microstructure_acceleration` (short-horizon velocity+acceleration
    alignment, excluding COMPRESSION/ERRATIC/UNKNOWN regimes as too
    noisy for a short read).
  - `build_active_registry()`: the single source of truth for which
    strategies are active — exactly the six, freshly constructed per
    call, no shared mutable state.
  - `tests/test_strategies.py` (43 tests) + `tests/test_strategy_registry.py`
    (21 tests): per-strategy fire/FLAT conditions, `StrategySignal`
    validation (including an explicit no-money/volume-field assertion),
    and the retirement firewall parametrized over BOTH retired keys
    (not just one) — proving a rejected registration doesn't corrupt
    subsequent valid ones, and that `build_active_registry()`'s key set
    is exactly the expected six and disjoint from the retired set.
- **TESTED (live)**: ran the complete active registry (all six
  strategies, 18,000 strategy×bar evaluations) against 3000 real,
  causally-walked XAUUSD M5 bars — zero crashes; signal frequency varied
  sensibly per strategy, consistent with the regime distribution
  observed in the earlier live regime-classifier run (RANGE-dominant
  data producing far more statistical_reversion/microstructure_
  acceleration signals than momentum/breakout/volatility_expansion ones).
- Full suite: 311 passed, 0 failed, 0 skipped.
- Committed as `c085b6f` and pushed to `origin/main`.

## 2026-09-20 (new session)

- Resumed from a fresh session. Verified local working tree: one
  untracked file beyond the last push (`c085b6f`) —
  `adaptive_scalper/persistence/migrations/0006_journal.sql`, work in
  progress from the previous session's start on the decision journal.
- Building 0006's immutability trigger (`CREATE TRIGGER ... BEGIN ...
  END;`) exposed the exact defect BUG_BACKLOG.md had flagged as a known,
  open limitation: the migration splitter's naive `;`-split cannot
  safely parse a trigger body's internal semicolons. Rather than route
  around it (e.g. special-casing 0006, or weakening the trigger), fixed
  the general parser:
  - `adaptive_scalper/persistence/database.py`'s `_split_statements()`
    rewritten around `sqlite3.complete_statement()` — SQLite's own C
    library statement-boundary oracle. Scans character by character;
    each `;` triggers a completeness check against the accumulated
    buffer, so a `;` inside a quoted string, inside a `--` comment, or
    inside a trigger's `BEGIN...END` body correctly keeps accumulating
    instead of splitting. This also let the previous separate
    comment-stripping regex be removed entirely — `complete_statement()`
    already handles comments correctly.
  - `tests/test_migration_parser.py` (12 tests): ordinary/multiple
    statement splitting, two statements on one physical line, a
    semicolon inside a quoted string literal, a semicolon inside a
    comment, a two-internal-statement trigger body emitted as exactly
    one statement, an end-to-end migration (CREATE TABLE + CREATE
    TRIGGER + CREATE TABLE) that both applies AND whose trigger
    genuinely blocks a real UPDATE, transactional rollback of earlier
    statements when a later one fails, incomplete trailing SQL raising
    `MigrationError` rather than silently vanishing, and idempotency.
  - Full suite (all prior migrations 0001-0005 still apply correctly
    under the new parser): 323 passed, 0 failed, 0 skipped.
  - Live-verified end-to-end: applied migration 0006 against a fresh
    in-memory database alongside 0001-0005, inserted a real
    `journal_events` row, then confirmed a direct `UPDATE` and a direct
    `DELETE` against it both genuinely raise `sqlite3.IntegrityError`
    with the trigger's "append-only" message — not merely "no test
    caught a problem."
  - Marked BUG_BACKLOG.md's naive-splitter entry (and its earlier
    comment-semicolon sub-fix, which this supersedes) as fully Fixed,
    with the implementation and exact test list recorded.
- Completed `adaptive_scalper/journal/` (directive sections 54-58):
  - `events.py`: `append_event()` — the only write path; no
    update/delete function exists in the module at all. `sequence_in_chain`
    is computed as `1 + MAX(existing)` and inserted together with the
    row inside one `BEGIN IMMEDIATE` transaction, closing a TOCTOU race
    a plain autocommit read-then-write would leave open under concurrent
    callers. `EVENT_TYPES` (27 types — directive's full list plus
    `ORDER_PENDING`/`ORDER_CANCELLED`/`ORDER_EXPIRED`, added after
    the pasted mission listed them explicitly) is checked in Python
    before ever reaching the database's own `CHECK` constraint, for a
    specific `UnknownEventTypeError` instead of a generic
    `sqlite3.IntegrityError`.
  - `queries.py`: `get_chain_events()` (full causal chain, in order),
    `get_events_by_type()`, `get_events_for_broker_order()`.
  - Migration `0006_journal.sql`: `decision_chains` + `journal_events`,
    with strongly-typed indexed linkage columns (`broker_symbol`,
    `strategy_key`, `client_request_id`, `broker_order_id`,
    `broker_position_id`, `broker_deal_id`) alongside a `payload_json`
    column for event-specific fields belonging to subsystems that don't
    exist yet (cost, RAG, models) — avoiding a dozens-of-permanently-NULL-
    columns schema.
  - `tests/test_journal.py` (18 tests): append/ordering,
    cross-chain independence, linkage+payload round-trip, both query
    functions, unknown-event-type rejection (nothing partially written),
    every directive-named event type accepted, `get_or_create_chain`
    idempotency and its cross-symbol-reuse guard, both immutability
    triggers actually firing, the point-in-time-immutability principle
    itself (an earlier event's payload is provably unaffected by a later
    contradicting one — directive section 54's core requirement, not
    just "no update function exists"), restart persistence via a fresh
    connection, and atomic chain-creation-plus-insert.
  - Full suite: 341 passed, 0 failed, 0 skipped.
- **TESTED (live)**: ran the complete features → regime → strategy →
  journal pipeline against 500 real, causally-walked XAUUSD M5 bars,
  journaling every real `StrategySignal` the six active strategies
  produced (391 `SIGNAL_CREATED` events, `microstructure_acceleration`
  dominant — consistent with its earlier-observed higher firing
  frequency) into the ACTUAL persistent `data/adaptive_scalper.sqlite3`
  database with migration 0006 genuinely applied, then read them back via
  `get_events_by_type()`. This is exploratory verification data in a
  gitignored local database, not a claim of any production trading
  activity.
- Committed as `0f0f633` and pushed to `origin/main`.
- Built `adaptive_scalper/news/` (directive sections 38-44), continuing
  automatically per the mission's explicit instruction not to stop at
  the journal or any phase boundary:
  - Confirmed real outbound network access works in this environment,
    then investigated the directive's named PRIMARY provider,
    "FinanceCalendar." A web search found no genuine, distinct,
    official, keyless, structured-JSON economic-calendar service by
    that name. Rather than fabricate an integration, used directive
    section 138's own acceptance-checklist escape valve
    ("FinanceCalendar primary implemented OR actual limitation
    documented"): `providers/financecalendar.py` is an explicit,
    documented stub whose `fetch()` always raises immediately with no
    network call.
  - Found and verified the REAL public Forex Factory JSON feed
    (`nfs.faireconomy.media/ff_calendar_thisweek.json`) — no API key,
    structured JSON, not HTML scraping. Confirmed its real schema
    (title/country/date/impact/forecast/previous) and, on a second live
    call minutes later, a genuine HTTP 429 "Rate Limited" response —
    real, first-hand evidence for exactly the provider-failure case
    `ProviderError` exists to surface rather than treat as "no events."
  - `blocking.py`: pure, no-I/O decision logic. Calendar
    outage/staleness/conflict checked BEFORE the event-window logic
    (directive section 43). Systemic FOMC/CPI/NFP events block all
    three symbols; other HIGH-impact events block only via each
    symbol's currency relevance (XAUUSD/BTCUSD: USD; GBPJPY: GBP, JPY).
    Window is 15-min-pre/30-min-post by default. First implementation
    used an inclusive window end; directive section 41's own worked
    example (16:30 event, 17:00:00 must be clear) caught this wrong via
    a dedicated regression test before anything else touched the code —
    fixed to inclusive-start/exclusive-end, matching the example
    exactly.
  - `calendar_service.fetch_with_fallback()`: tries every live provider
    (enables PRIMARY/SECONDARY conflict cross-checking, not just
    first-success), persists every success to the new `news_events`
    cache (migration `0007_news.sql`), falls back to cache only when
    every live provider fails, and reports `UNAVAILABLE` rather than a
    silent empty result when even the cache can't help.
  - `providers/manual.py`: optional operator-supplied normalized JSON
    fallback (never used automatically).
  - 68 new tests across `test_news_blocking.py` (34, no network),
    `test_news_providers.py` (26, HTTP mocked via monkeypatching
    `httpx.get` — no real network calls in the test suite itself),
    and `test_news_calendar_service.py` (8).
  - Full suite: 401 passed, 0 failed, 0 skipped.
- **TESTED (live)**: ran the complete real fallback chain against the
  actual persistent database with migration 0007 applied — FinanceCalendar
  correctly failed and fell through; ForexFactory returned 105 real
  events (16 real HIGH-impact), correctly identifying a genuine upcoming
  FOMC week. Verified the real block timeline around that real event:
  ALLOW 20 minutes before, BLOCK from 10 minutes before through 35
  minutes after — the continued block past the expected 30-minute
  post-window was investigated, not assumed to be a bug, and traced to a
  second, genuinely distinct real event in the same feed ("FOMC Press
  Conference," scheduled exactly 30 minutes after the Statement) whose
  own window correctly extended the block. This is real production news
  data exercising a real overlapping-events case none of the synthetic
  unit tests happened to construct.
- Committed as `fbdb128` and pushed to `origin/main`.
- Built `adaptive_scalper/costs/` (directive section 34), continuing
  automatically:
  - `model.py`: `estimate_cost()` (spread + commission + slippage +
    swap + uncertainty margin, margin provably monotonic — can only
    increase total cost) and `price_equivalent_of_monetary_cost()`,
    which converts a flat per-lot monetary cost (commission, swap) into
    a price distance using the SYMBOL'S OWN `trade_tick_size`/
    `trade_tick_value` rather than one generic forex constant (directive
    section 34's explicit requirement).
  - `edge.py`: `expected_gross_edge_price()` — a standard
    confidence/stop/target expected-value formula computed directly from
    the strategy's own hypothesis (Stage 0, no model/RAG yet — directive
    section 61). `evaluate_cost_gate()` distinguishes `BLOCK_COST`
    (costs couldn't be determined) from `BLOCK_EXPECTED_EDGE` (costs
    known, net edge insufficient) from `ALLOW`.
  - `tracking.py` + migration `0008_costs.sql`: estimated-vs-realized
    cost tracking with `prediction_error`, ready for the execution layer
    to call once it exists; a realized cost can be recorded once, never
    overwritten.
  - 29 new tests across three files: conversion math, component
    validation, the EV formula hand-checked against manual arithmetic,
    all three gate outcomes, prediction-error sign in both directions,
    double-recording rejection, restart persistence.
  - Full suite: 429 passed, 0 failed, 0 skipped.
- **TESTED (live)**: computed a real cost estimate from the live XAUUSD
  contract spec (point=0.01, tick_size=0.01, tick_value=$1) with a
  $7/lot commission assumption — the price-equivalent conversion matched
  the unit-tested formula exactly on real data. Ran the complete
  features → regime → strategy → cost → edge pipeline against real M5
  bar history and found real signals on both sides of the gate: two
  low-confidence microstructure_acceleration signals (~0.16, right at
  the strategy's own minimum threshold) correctly resulted in
  `BLOCK_EXPECTED_EDGE` with negative net edge; four higher-confidence
  signals correctly `ALLOW`ed with positive net edge — hand-verified the
  EV arithmetic against the printed numbers for one case
  (raw_confidence=0.651, stop=4.09, target=6.13 → EV≈2.7, minus real
  cost≈0.54 → net≈2.38, matching the pipeline's own 2.3755 output).
- Committed as `37ec261` and pushed to `origin/main`.
- Built `adaptive_scalper/portfolio/` (directive section 35), continuing
  automatically:
  - `correlation.py`: Pearson correlation over ALIGNED observations —
    `{timestamp: return}` dicts intersected on shared timestamps, never
    two equal-length series zipped positionally (a deliberate test,
    `test_mismatched_timestamps_never_silently_zipped`, proves this:
    two 40-observation series with completely disjoint timestamps align
    to exactly 0 shared points, not 40). Reports `None` ("N/A") for
    insufficient sample size or zero-variance series, never a
    fabricated `0.0` (directive's explicit requirement).
    `evaluate_correlation_gate()` blocks a new proposal only on a
    genuinely measured high correlation with an already-open symbol.
  - `exposure.py`: `compute_exposure()` -- open/pending risk, per-symbol
    exposure, net currency-direction exposure. Uses a
    portfolio-accounting-specific canonical currency-pair map,
    explicitly documented as distinct from
    `symbol_validation.EXPECTED_IDENTITY` (that module verifies
    broker-reported metadata and deliberately avoids claiming BTCUSD's
    broker `currency_base` is "BTC" -- a different, unrelated question
    from "what's the idealized long/short exposure of holding BTCUSD").
    `correlated_cluster_exposure()` combines exposure across
    open+correlated symbol pairs.
  - 30 new tests across both files.
  - Full suite: 459 passed, 0 failed, 0 skipped.
- TESTED (live): computed real pairwise correlations across all three
  canonical symbols' full M5 return history from the earlier bootstrap.
  This surfaced a genuine, meaningful confirmation of why
  timestamp-alignment (not positional zipping) matters: BTCUSD trades
  24/7 while XAUUSD/GBPJPY only trade market hours, so real aligned
  sample sizes differed substantially by pair (68,855 to 95,130
  observations) rather than being a uniform count -- exactly the
  divergence a naive same-length zip would have masked. All three real
  correlations came out low-to-modest (0.07 to 0.23).
- Committed as `2721650` and pushed to `origin/main`.
- Built `adaptive_scalper/risk/` (directive sections 32-33), completing
  every individual Phase 3 gate dependency:
  - `calculate_safe_volume()`: the sole sizing authority, computing
    volume from CURRENT equity/stop/contract data only -- no
    "previous volume"/"loss streak"/"multiplier" parameter exists in its
    signature at all, making martingale/grid/revenge-sizing structurally
    impossible rather than merely discouraged. A dedicated test inspects
    the actual function signature for forbidden parameter-name
    substrings, so this guarantee can't silently erode from a future
    edit without a test catching it. Rounds DOWN to volume_step; rejects
    (never rounds up) when that falls below volume_min.
  - `evaluate_risk_gate()`: the five hard ceilings
    (max_open_positions, max_positions_per_symbol,
    max_total_open_risk_pct, max_daily_loss_pct, max_drawdown_pct)
    checked in a fixed order, so the reported block reason is always the
    first limit actually breached, not an arbitrary one.
  - `risk_limits_from_config()`: the only intended construction path for
    RiskLimits, built from the existing validated RiskConfig -- honestly
    documented as an import-discipline boundary (same pattern as
    operator_authority.py's kill-switch boundary) since no ML/learning
    module exists yet that could attempt to raise these limits.
  - 22 new tests. One test's own tolerance was too tight on first run
    (a rounding-discretization difference of $1 at these numbers,
    correctly computed, just asserted too strictly) -- fixed the test,
    not the implementation, after confirming by hand that $24 and $25
    were both correct roundings of the same $25 risk budget at different
    stop distances.
  - Full suite: 481 passed, 0 failed, 0 skipped.
- TESTED (live): computed a real safe-volume result from the actual DEMO
  account's real equity ($9,707.85) and XAUUSD's real contract spec with
  a real stop distance from an earlier live strategy signal -- result:
  0.05 lots, $20.45 monetary risk (~0.21% of equity after round-down,
  consistent with the 0.25% target), correctly ALLOWed by the risk gate.
- Every individual Phase 3 gate dependency now exists and is
  live-verified: features, regime, strategies, journal, news, cost/edge,
  correlation/portfolio, risk. Next task is composing the full final
  permission gate (directive section 36) from all of them.

## 2026-09-20 (new session, continued)

- Resumed from a fresh session. Confirmed local working tree had one
  uncommitted file beyond the last push (`5e61a45`):
  `adaptive_scalper/core/final_permission.py`, work in progress from the
  previous session's final minutes composing the final permission gate.
- Before finishing composition, addressed 4 further external-review
  findings on the risk/cost/correlation modules:
  1. `risk/governor.py`'s `evaluate_risk_gate()` now independently
     re-verifies the per-trade risk ceiling
     (`proposed_monetary_risk <= equity * risk_per_trade_pct/100`,
     plus positive/finite/positive-equity checks) as its FIRST check,
     rather than trusting `calculate_safe_volume()` was correctly used
     upstream. `RiskGateInput` gained `current_total_pending_risk`, and
     the total-risk ceiling is now `open + pending + proposed`. 8 new
     tests, including a deliberately-oversized-proposal case with an
     otherwise pristine portfolio.
  2. `costs/model.py`'s `estimate_cost()` lost its `0.0` defaults for
     commission/slippage/swap — all four components are now required
     keyword arguments, so a caller who forgets one gets an immediate
     `TypeError` instead of a silent "this is exactly zero." New
     `estimate_cost_from_evidence()` is the required real-runtime entry
     point: `float | None` per component, returns `None` (→ `BLOCK_COST`
     via the existing `evaluate_cost_gate()`) if anything is unknown.
     8 new tests. Updated ~15 existing test call sites across
     `test_cost_edge.py`/`test_cost_tracking.py` that relied on the old
     defaults (scripted via a small Python regex pass rather than
     hand-editing each one, then verified by running the suite).
  3. `portfolio/correlation.py`'s `evaluate_correlation_gate()` gained
     `treat_missing_as_blocking` (default `True`): when another open/
     pending position exists and correlation against it is genuinely
     N/A, the gate now blocks (`BLOCK_CORRELATION`) rather than treating
     unresolved correlation as evidence of safety — `compute_pairwise_
     correlation()`'s own honest N/A reporting is unchanged, this is
     purely about what the GATE does with that honest N/A.
     `treat_missing_as_blocking=False` preserves the old behavior for
     non-decision-making callers. Updated the one existing test that
     assumed the old default; added 1 new test for the opt-out path.
  4. Cleaned real staleness in `PROJECT_STATUS.md` flagged by the
     review: "Current phase" still said Phase 3 hadn't started (it was
     essentially complete by the previous session's end); the
     persistence section still said "schema at version 5" (actually 8)
     and described the migration splitter as a tracked, unfixed defect
     (it was fully fixed two sessions ago); `core/permission.py`'s
     description still said no composed gate existed. All rewritten to
     match current reality rather than left as contradictions.
  - Full suite after all 4 fixes: 498 passed, 0 failed, 0 skipped (17
    new tests).
- Finished and tested `adaptive_scalper/core/final_permission.py`
  (directive section 36): `evaluate_final_permission()` composes canonical
  symbol allow-list, retired-strategy firewall (independent final-gate
  defense per directive section 8, not just relying on the strategy
  registry's own block), DEMO verification, kill switch, asset identity/
  direction, execution-grade quote freshness, news, cost/edge,
  correlation, and risk — in one fixed-order deterministic function, pure
  (no I/O, matching every other gate already built).
  `evaluate_and_journal_final_permission()` wraps it with a real
  `ENTRY_ALLOWED`/`ENTRY_BLOCKED` journal write. Honestly documented
  named gaps for subsystems that don't exist yet
  (`BLOCK_RECONCILIATION`/`BLOCK_UNKNOWN_ORDER`/`BLOCK_MARGIN`/
  `BLOCK_BROKER_CONSTRAINT`/`BLOCK_DUPLICATE`/`BLOCK_REENTRY_CHURN`/
  `BLOCK_PORTFOLIO_RISK`) rather than fabricated always-clean defaults.
  `tests/test_final_permission.py` (23 tests, all passed on first run):
  happy path, every individual block reason, fixed-order verification,
  both journaling outcomes.
  Full suite: 521 passed, 0 failed, 0 skipped.
- **TESTED (live) — capstone verification**: ran the COMPLETE real
  pipeline in one script against the live DEMO account and real market
  data: MT5 DEMO verification, real symbol resolution/identity/
  direction/quote checks, a real strategy signal found by walking real
  M5 bar history (microstructure_acceleration, confidence 0.698), a real
  news check (ALLOW), a real cost estimate from the live spread + a
  $7/lot commission assumption, a real correlation matrix from real
  aligned returns, and a real safe-volume calculation from the live
  account's actual equity ($9,707.85 → 0.06 lots / $22.46 risk) — then
  ran the composed gate twice: once against the REAL persistent kill
  switch state (`UNINITIALIZED`, never operator-bootstrapped in this
  database), which correctly returned `BLOCK_KILL_SWITCH` — proving the
  gate honestly respects real safety state and was not bypassed for the
  test — and once against a hypothetical `DISENGAGED` `KillSwitchState`
  constructed only in memory for this verification (never written to the
  real database), under which every other real gate passed and the
  result was `ALLOW`. The real runtime kill switch was NOT cleared or
  bootstrapped at any point.
- Committed as `8340975` and pushed to `origin/main`.
- Built `adaptive_scalper/execution/` (directive sections 29-31),
  continuing automatically without stopping — the safety layer that
  must exist and be tested BEFORE `order_send` is ever added, per the
  mission's explicit lock:
  - `state_machine.py`: `OrderState` (12 states) + `ALLOWED_TRANSITIONS`.
    Broker acknowledgement is deliberately NOT a fill —
    `SUBMITTED -> FILLED` directly is an illegal transition;
    `apply_transition()` raises rather than silently permitting it.
  - `store.py`: `create_order()` idempotent on `client_request_id` — a
    second call with the same id returns the EXISTING row unchanged,
    even when called with entirely different parameters (tested
    explicitly). `transition_order_state()` is the only path that
    changes an order's state, always validated through the state
    machine first, with the initial `PROPOSED` creation itself recorded
    as a transition so an order's full lifecycle — not just its current
    state — is always reconstructable.
  - `unknown.py`: `resolve_unknown_order()` resolves against
    `history_orders_get()` (already implemented, already live-tested).
    Explicitly honest about its current limit: without
    `positions_get`/`orders_get` (which don't exist yet), an order still
    genuinely resting on the broker with no history entry yet correctly
    reports `resolved=False` rather than guessing.
  - `reconciliation.py`: `reconcile_positions()` (broker truth
    authoritative — orphan broker position / missing local position /
    volume-or-direction mismatch) and `has_dangerous_unresolved_unknown()`
    for the "block new entries" check directive section 30 requires.
    `BrokerPositionSnapshot` is an explicit, documented stand-in for
    `positions_get()`'s future real output.
  - Migration `0009_execution.sql`: `orders`, `order_state_transitions`,
    `deals`, `positions`, `execution_incidents`.
  - 52 new tests across four files, all passing on first run.
  - Full suite: 573 passed, 0 failed, 0 skipped.
- **TESTED (live)**: fetched 2,234 real orders from the live DEMO
  account's order history (2,218 genuinely `FILLED`), built a simulated
  `UNKNOWN` local order referencing one real broker ticket, and
  confirmed `resolve_unknown_order()` correctly resolved it to `FILLED`
  with the correct `broker_position_id` — the resolution logic works
  against the real shape of broker history data, not just synthetic
  fixtures.
- Committed as `6876b73` and pushed to `origin/main`.
- Built `adaptive_scalper/selector/` — the strategy selector, combining
  the six strategies' candidate signals into one proposal or FLAT,
  ranked by COST-ADJUSTED expected net edge rather than raw confidence
  (the directive's explicit "do not simply choose the highest raw
  confidence"). `cost_estimates` keyed per canonical_symbol, since
  candidates may span more than one symbol in a cycle and cost genuinely
  differs. Retired keys rejected independently (defense-in-depth).
  `select_and_journal_proposal()` journals every candidate's outcome
  (`SIGNAL_REJECTED`/`PROPOSAL_REJECTED`/`PROPOSAL_CREATED`) to its own
  chain. 13 new tests, including a constructed higher-confidence-but-
  worse-net-edge-loses case with hand-checked EV numbers.
  Full suite: 586 passed, 0 failed, 0 skipped.
- **TESTED (live)**: walked real XAUUSD M5 bars looking for cycles where
  2+ strategies fired simultaneously — found several real cases,
  including one genuine FLAT result (neither real candidate actually
  cleared the cost/edge bar despite both looking plausible at a glance)
  and others where the selector correctly picked the real higher-edge
  candidate between two real overlapping signals.
- Committed as `96a2211` and pushed to `origin/main`.
- Extended the gateway with `positions_get`/`orders_get`/`order_check`/
  `order_send` — deliberately withheld until now (directive section
  118), added only once the execution safety layer existed to receive
  results safely:
  - New broker-independent types: `OrderAction`, `OrderRequest`,
    `OrderSendResult`, `OrderCheckResult`, `PositionSnapshot`,
    `PendingOrderSnapshot`.
  - `Mt5Gateway._build_mt5_request()` is the ONLY place in the codebase
    that constructs MetaTrader5's raw request dict; `_order_send_result()`
    parses the raw response (zero deal/order tickets become `None`,
    never a fake `"0"` id).
  - `FakeGateway` gained a full in-memory order/position simulation:
    `order_send()` auto-fills a DEAL into a tracked position, applies
    SLTP, cancels a matching pending REMOVE — or returns exactly queued
    `order_send_responses` for reject/UNKNOWN/partial-fill test
    scenarios.
  - 30 new tests: `test_mt5_request_builder.py` (15, no real SDK needed
    — tested against a fake stand-in module with the same named
    constants) and `test_gateway_execution.py` (15, including a
    deliberate ticket-mismatch case proving REMOVE only matches the
    exact ticket).
  - Full suite: 616 passed, 0 failed, 0 skipped.
- **TESTED (live), READ-ONLY ONLY**: `positions_get()`/`orders_get()`
  called against the real DEMO terminal — both correctly returned empty
  (this account has never had an order placed against it). **Deliberately
  did NOT call `order_send()`/`order_check()` against the real terminal
  this session** — doing so would place a real (if DEMO) order before the
  PAPER run and full QA campaign the directive requires first. This is a
  conscious scope boundary: the code exists and is thoroughly tested
  against `FakeGateway`, but nothing in this codebase has invoked real
  `order_send` yet.

## Session: execution-safety review fixes + position management

Resumed from commit `ae14892` (pushed). An external execution-safety
review of the Phase 4 building blocks found 9 issues that had to be
fixed before controlled-DEMO execution could be considered. Fixed all
nine, in order:

1. **Order ticket ≠ position ticket.** `_order_send_result()` (both
   `Mt5Gateway` and `FakeGateway`) no longer invents `broker_position_id`
   from the order ticket — it's always `None` straight off `order_send()`,
   matching real MT5's `MqlTradeResult`. New
   `execution/position_resolution.resolve_opened_position_id()` recovers
   the true position id from `history_deals_get()`/`history_orders_get()`
   afterward (deal.position_id preferred, order.position_id fallback).
   `FakeGateway` now mints three distinct tickets (order/deal/position)
   per fill and records a matching `HistoricalDeal` so the simulation
   can't teach tests order-id == position-id. Regression:
   `test_deal_order_send_position_ticket_differs_from_order_and_deal_ticket`,
   `test_position_id_resolvable_from_history_deals`,
   `test_order_send_result_never_invents_a_position_id`, and all of
   `tests/test_position_resolution.py` (6 tests, including an
   end-to-end round trip through `FakeGateway`'s own simulation).
2. **Safe position close path.** New `execution/close.py`
   (`close_position_safely()`): fresh `positions_get()` immediately
   before send; `ALREADY_CLOSED` if the position is gone (never sends an
   opposite trade against nothing); `VOLUME_MISMATCH` if broker
   direction/volume disagree with the caller's expectation (partial
   close stays disabled — this only ever closes the broker's exact
   current volume). `OrderRequest.position_ticket` on a DEAL now means
   "close this exact position" — `_build_mt5_request` sets MT5's
   `position` field; `FakeGateway._simulate_close_deal` mirrors it
   without opening a new position. 8 tests in
   `tests/test_execution_close.py`, including the exact race condition
   (broker-side SL close between decision and send) and a broad
   regression proving open-position count can never increase across any
   outcome.
3. **UNKNOWN resolution upgraded.** `execution/unknown.py` rewritten to
   resolve against `positions_get`/`orders_get`/`history_orders_get`/
   `history_deals_get` together, not order history alone. Agreeing
   evidence resolves; CONFLICTING evidence (e.g. history says REJECTED
   but a live broker position exists for the same order) is never
   guessed away — `conflict=True`, unresolved, forces
   PENDING_RECONCILIATION. `tests/test_execution_unknown.py` rewritten
   (19 tests) with new coverage for current-state evidence and both
   conflict scenarios.
4. **Reconciliation upgraded to real gateway truth.**
   `execution/reconciliation.run_reconciliation()`: fetches
   `positions_get()`, compares to local OPEN positions, classifies to
   one deterministic status (`CLEAN`/`BLOCKING_MISMATCH`/`RECOVERED`)
   via new `classify_reconciliation()`, records `execution_incidents`
   for blocking findings, journals `RECONCILIATION_ACTION` every run.
   10 new tests.
5. **Final permission gate integrates execution safety.**
   `FinalPermissionInput` gained `reconciliation_status`,
   `has_dangerous_unknown_order`, `duplicate_active_order`,
   `reentry_check` — all REQUIRED (no fake-clean default). New gate
   checks: `BLOCK_RECONCILIATION`, `BLOCK_UNKNOWN_ORDER`,
   `BLOCK_DUPLICATE`, `BLOCK_REENTRY_CHURN`. Docstring rewritten — no
   longer describes execution/gateway pieces as nonexistent.
   `BLOCK_MARGIN`/`BLOCK_BROKER_CONSTRAINT` deliberately live one step
   later (`execution/service.py`, needs the exact request + fresh
   `order_check`) — documented, not silently missing.
   `BLOCK_PORTFOLIO_RISK` remains an honest, still-open gap. 10 new
   tests including gate-ordering checks.
6. **Idempotency collision fails loudly.** `execution/store.py` gained
   `IdempotencyConflictError` — a `client_request_id` collision on a
   DIFFERENT immutable field (symbol, direction, volume, SL, TP,
   chain_key, broker symbol) now raises instead of silently returning
   the stale row. 7 new tests, one per field.
7. **Broker filling-mode constraints.** New
   `gateway/broker_constraints.derive_filling_type()` reads the new
   `SymbolSpec.filling_mode` bitmask and picks a broker-supported policy
   (IOC preferred, FOK fallback) instead of hardcoding
   `ORDER_FILLING_IOC`; returns `None` if neither is supported.
   `OrderRequest.filling_type` threads the resolved value through
   `_build_mt5_request`.
8-9. **`execution/service.py`** (NEW) — the ONE execution orchestration
   service: idempotent PROPOSED order → FRESH
   `evaluate_and_journal_final_permission` (never cached) → resolve
   filling type → exact `OrderRequest` → mandatory `order_check` →
   SUBMITTED → `order_send` (same unmutated request) → interpret result
   through the state machine → resolve position via
   `position_resolution` → FILLED/UNKNOWN/REJECTED → full journal
   lifecycle. New architectural regression test
   `tests/test_architecture_execution_boundary.py` scans
   `strategies/`/`selector/`/`learning/`/`rag/`/`dashboard/`/`cli/` for
   direct `.order_send(` references and fails the build if found — only
   `execution/` and `gateway/` may reference it. 9 tests in
   `test_execution_service.py` covering the happy path, every block
   branch, broker rejection, and unresolvable-UNKNOWN.
- **TESTED (live), READ-ONLY ONLY**: real DEMO terminal (login number
  withheld per precedent — account-identifying info, not committed;
  server `ICMarketsSC-Demo`, `trade_allowed=True`, `trade_expert=True`,
  `account_info().trade_mode == DEMO`). Confirmed all three canonical
  symbols report `filling_mode=2` (IOC-only on this broker) and
  `derive_filling_type()` correctly resolves `IOC` for each through the
  real `Mt5Gateway.symbol_info()`. Confirmed `positions_get()`/
  `orders_get()` both still return empty through the real gateway.
  Deliberately did NOT call `order_check()`/`order_send()` live — tracked
  as BUG_BACKLOG.md item 5 (the `order_check` success-retcode convention
  needs live verification before controlled DEMO).

Then built `position_management/adaptive_exit.py` and `re_entry.py`
(directive sections 17-23, 26-28):

- `adaptive_exit.py`: `evaluate_adaptive_exit()` — fixed priority order
  (max holding time → thesis invalidated → regime reversed → early take
  profit → profit-giveback protection → breakeven advancement → HOLD),
  directive's exact default parameters, `resolve_new_stop_price()`
  enforcing "never move a protective stop backward." Found and fixed a
  real float-precision bug during testing: `peak_r - current_r` at exact
  threshold boundaries (e.g. `0.85 - 0.65`) evaluates to
  `0.19999999999999996` in binary floating point, not `0.20` — the
  giveback comparison needed the same epsilon-tolerance pattern already
  used in `risk/governor.py`'s volume-step rounding. 23 tests.
- `re_entry.py`: `evaluate_reentry()` — cooldown first, then same-
  direction (elevated confidence bar, `max(same_direction_threshold,
  original + min_improvement)`) vs. different-direction (treated as a
  genuinely new setup, only the normal bar applies). Wired into
  `core/final_permission.py` via the new `reentry_check` field. 10 tests.
- `expectancy.py` (continuous position expectancy re-evaluation,
  directive section 17) is explicitly scoped OUT this session — tracked
  as BUG_BACKLOG.md item 6. `evaluate_adaptive_exit()`'s
  `thesis_valid`/`regime_reversed` inputs are accepted as pre-computed
  evidence; nothing yet computes that evidence from live state.

Full suite: 723 passed, 0 failed, 0 skipped (up from 616). Committed as
`60aa7d0` and pushed to `origin/main`.

Then built local RAG (`adaptive_scalper/rag/`, directive: bounded
advisory memory):

- Migration `0010_rag.sql`: `rag_memories` table (8 memory types:
  `TRADE_SETUP`/`TRADE_RESULT`/`REJECTION`/`EXIT_DECISION`/
  `REENTRY_DECISION`/`EXECUTION_INCIDENT`/`STRATEGY_CONTEXT`/
  `SYSTEM_EVENT`). Schema version now 10.
- Installed `scikit-learn` (+ `scipy`/`joblib`/`threadpoolctl`/
  `cloudpickle`/`narwhals` transitive deps) and pinned exact versions in
  `requirements.txt`, per the project's own "add a dependency only when
  its module actually lands" convention — RAG is the first real
  consumer.
- `rag/store.py`: plain-insert-only persistence, no update/delete API.
- `rag/index.py`: `RagIndex` — a derived, REBUILDABLE, in-memory
  TF-IDF/cosine-similarity index; no on-disk index file, so it can never
  drift from the DB; `rebuild()` re-fits from scratch on demand.
- `rag/service.py`: `RagService` — the intended public entry point.
  Every failure mode degrades to `DEGRADED` (never an unhandled
  exception reaching a real caller). Structurally advisory-only:
  `record()`/`query_similar()` accept no `Gateway`/risk/kill-switch/
  permission-authority parameter at all — verified by a signature-
  inspection test (same pattern as `calculate_safe_volume()`'s
  martingale-impossibility guarantee) and an AST-level import check
  proving none of the three RAG modules import `gateway`/
  `core.kill_switch`/`risk`.
- 33 tests: `test_rag_store.py`, `test_rag_index.py`, `test_rag_service.py`.
- NOT yet wired into the real pipeline: nothing calls `RagService
  .record()` from the journal/selector/position-manager yet, and no CLI
  `rag *` commands exist — both are part of the still-pending
  runtime-wiring and CLI-completion tasks.

Full suite: 748 passed, 0 failed, 0 skipped. Committed as `91e9700` and
pushed to `origin/main`.

Then built ML/self-learning observer-stage machinery
(`adaptive_scalper/learning/`, directive: observer stage first, no
source self-modification, no eval/exec):

- Migration `0011_learning.sql`: `models` + `model_lifecycle_transitions`
  tables. Schema version now 11.
- `learning/lifecycle.py`: `ModelLifecycleState` 8-state enum +
  `ALLOWED_TRANSITIONS`, mirroring `execution.state_machine`'s design
  exactly (broker-acknowledgement-is-not-a-fill's ML analogue: a
  `CHALLENGER` promotion is not conflated with actually becoming
  `CURRENT`).
- `learning/registry.py`: `register_model()`/`transition_model_state()`,
  auto-incrementing versions (never caller-supplied, avoids races), full
  lifecycle history. Retired strategy keys refused at BOTH registration
  and promotion-to-CURRENT independently (defense in depth, directive
  section 8's established pattern) — regression test simulates a key
  being retired AFTER a model was registered for it, proving the
  promotion-time check isn't redundant.
- `learning/promotion.py`: `evaluate_promotion_gate()` — pure,
  fail-closed, every directive-named requirement (min samples, causal
  features, temporal/purged split, walk-forward, untouched OOS,
  realistic costs, calibration, subgroup stability, artifact checksum,
  rollback availability) is a REQUIRED evidence field with no default.
  Does not itself run training/evaluation — that's the backtest/
  walk-forward subsystem's job, still pending; this is the decision core
  its results feed into.
- `learning/drift.py`: `apply_drift_response()` — structurally guarantees
  drift only ever lowers influence, verified by a property-style test
  across a 7×2×7 grid of weight/detected/severity combinations, not just
  a couple of examples.
- `test_learning_structural_safety.py`: AST-level checks — no
  `eval`/`exec`/`compile`/`__import__` anywhere in `learning/`, no
  import of `gateway`/`core.kill_switch`/`execution`, and
  `learning.registry`'s functions carry no risk-sizing-shaped parameter.
- NOT yet implemented: actual model training (needs the backtest/
  walk-forward subsystem's temporal-split machinery first — directive's
  own dependency order, not skipped by oversight).

Full suite: 799 passed, 0 failed, 0 skipped. Committed as `bebb540` and
pushed to `origin/main`.

## Session: round-2 execution-safety review (3 CRITICAL fixed)

Resumed from `bebb540` (verified clean, nothing newer locally). A second
external review of the execution layer found 8 more issues before any
real `order_send` could be considered. Fixed the 3 CRITICAL ones this
checkpoint (the 5 HIGH ones are tracked as BUG_BACKLOG.md items 0a-0d):

1. **Fresh pre-send safety, structurally enforced.**
   `execution/service.submit_new_entry()`'s signature changed from a
   plain `permission_input: FinalPermissionInput` parameter to
   `fetch_fresh_evidence: Callable[[], FreshEvidence]`. The function now
   calls this callable TWICE and re-runs
   `evaluate_and_journal_final_permission()` both times — once before
   `order_check`, and again immediately before `order_send`. A plain
   parameter can only be evaluated once, at whatever moment the caller
   built it; a callable structurally forces a second, independent
   evaluation this function itself controls the timing of. New
   `BLOCKED_PRESEND_RECHECK` status. 9 new regression tests, each
   simulating one volatile-evidence change (account leaves DEMO, kill
   switch engages, quote goes stale, a news window opens, reconciliation
   becomes blocking, an UNKNOWN appears, risk state moves, a duplicate
   appears) between the two evidence fetches, each asserting
   `gateway.order_send_calls == []` — proving the second check actually
   prevents the send, not just that the function returns a different
   status.
2. **Authoritative MT5 retcode mapping.** New `gateway/retcodes.py`:
   real `ENUM_TRADE_RETCODE` semantics, not a "10009 or REJECTED"
   binary. `DONE_PARTIAL` (10010) -> `PARTIAL` (real exposure at the
   actual filled volume, journaled, never rejected). `PLACED` (10008)
   -> `RESTING` (never rejected). `TIMEOUT`/`ERROR`/`CONNECTION` ->
   `UNKNOWN` (ambiguous transport outcome, conservative, never guessed
   into DONE or REJECTED, never blindly resent). Only retcodes with
   positive proof of rejection (`REQUOTE`, `REJECT`, `INVALID_*`,
   `TRADE_DISABLED`, `MARKET_CLOSED`, `NO_MONEY`, etc.) map to
   `REJECTED`. An unrecognized retcode (future SDK/broker value not in
   the table) also conservatively maps to `UNKNOWN`, never guessed.
   `execution/state_machine.py`'s `SUBMITTED` transitions widened to
   allow direct `PARTIAL`/`RESTING`/`CANCELLED` (order_send's own
   retcode can report these immediately — `SUBMITTED -> FILLED` directly
   remains illegal, broker acknowledgement of a full DONE still isn't a
   fill). 16 tests in `test_gateway_retcodes.py`, including explicit
   regressions proving 10008/10009/10010 can never become REJECTED and
   TIMEOUT can never become REJECTED or DONE.
3. **Safe close gets the same pre-send protections as a new entry.**
   `execution/close.close_position_safely()` rewritten around
   `verify_demo_before_order()` (genuinely fresh — it calls
   `terminal_info()`/`account_info()` itself every time, no caching
   layer to go stale) + fresh `positions_get()` + fresh `symbol_info()`
   -derived filling type, run TWICE (once before `order_check`, once
   again immediately before `order_send`), then `interpret_retcode()` on
   the result. Deliberately does NOT check the kill switch or run the
   full final-permission gate — directive: "risk reduction should remain
   available" even when new entries are blocked; only DEMO-account truth
   and freshly-proven position identity gate a close. Optional
   `conn`/`reconciliation_chain_key` params trigger a real reconciliation
   pass immediately after a `SENT` outcome. 14 tests, including the
   account-switches-to-REAL-mid-flight and position-disappears-mid-flight
   races, both proving zero additional `order_send` calls.

Also fixed the stale `PROJECT_STATUS.md` "Current phase" section
(round-2 finding #5) — it still claimed Phases 4-13 hadn't started and
`order_send` didn't exist, both false since the round-1 checkpoint.

Full suite: 837 passed, 0 failed, 0 skipped (up from 799). Committed as
`c0a40f2` and pushed to `origin/main`.

Continued in the same session with the remaining 5 HIGH findings from
the round-2 review:

4. **Reconciliation `RECOVERED` now does real repair.**
   `run_reconciliation()` calls new `find_closing_deal()` (queries
   `history_deals_get()` for the exact position id, picks the latest
   `entry==OUT` deal) and new `_recover_missing_local_position()`
   (atomically marks the local row `CLOSED` with the real close time,
   inserts the real `deals` row, all in one `BEGIN IMMEDIATE`
   transaction). `POSITION_CLOSED` gets journaled under a per-position
   sub-chain (`{chain_key}:position:{id}`) rather than the reconciliation
   run's own chain_key — a chain_key maps to exactly one canonical_symbol
   for its lifetime, and a reconciliation run is account-wide, so reusing
   the same chain_key for a symbol-specific `POSITION_CLOSED` event would
   have violated that invariant. `LocalPositionRecord` gained
   `opened_at_utc`/`entry_order_id` fields (needed to search a sensible
   history window and link the repair). If no closing deal is found,
   status stays `BLOCKING_MISMATCH` with an incident recorded — never
   silently relabeled. 4 new tests including one proving an unrepairable
   position is left untouched (`status='OPEN'`, no fabricated close).
5. **New `portfolio.exposure.evaluate_portfolio_risk_gate()`.**
   Simulates adding the proposed position to current open+pending
   exposure via the existing `compute_exposure()`/
   `correlated_cluster_exposure()` helpers, then checks total/per-symbol/
   net-currency-direction/correlated-cluster risk in a fixed order
   against a new `PortfolioRiskLimits` dataclass. New
   `portfolio_risk_limits_from_risk_limits()` convenience constructor
   sets every sub-ceiling equal to the existing global
   `max_total_open_risk_pct` (directive: "bounded by," never
   independently higher, not an arbitrary invented number). Wired into
   `core/final_permission.py` as `BLOCK_PORTFOLIO_RISK`, between the
   correlation and risk gates (matching the mission's stated check
   order). `FinalPermissionInput` gained `open_positions`/
   `pending_positions`/`portfolio_risk_limits` fields. 13 new tests.
6. **New `execution/request_token.py` + UNKNOWN secondary correlation.**
   `execution/service.py` now embeds a compact, deterministic
   `client_request_id`-derived token in every `OrderRequest.comment` it
   sends (MT5-comment-length-safe). New
   `execution.unknown.resolve_unknown_order_without_broker_id()` handles
   the case `resolve_unknown_order()` structurally cannot: a send that
   lost broker acknowledgement entirely, so `broker_order_id` was never
   recorded. Correlates on the token (narrowed by broker symbol) across
   current positions/pending orders/history orders/history deals,
   resolving ONLY when every matching candidate agrees on both state and
   position id — any disagreement is `conflict=True`, never guessed. 5
   new tests in `test_request_token.py`, 8 new in
   `test_execution_unknown.py`.
7. **New `position_management/expectancy.py`.**
   `evaluate_position_expectancy()` — the actual "if I were flat right
   now, would I still open roughly this exposure?" decision core
   directive section 17 asks for, producing the `thesis_valid`/
   `regime_reversed` evidence `adaptive_exit.evaluate_adaptive_exit()`
   consumes (no longer a caller-fabricated boolean, as flagged in
   BUG_BACKLOG.md item 6, now closed). Deliberately keeps RAG/ML evidence
   advisory-only — `rag_advisory_negative`/`model_advisory_negative` are
   recorded in `reasons` but can never, by themselves, flip
   `thesis_valid`, consistent with `rag/service.py`'s and `learning/`'s
   own established contracts (neither is authoritative). 12 tests.

Full suite: 880 passed, 0 failed, 0 skipped (up from 837). All 8 round-2
execution-safety findings are now fixed. Committed as `4109c37` and
pushed to `origin/main`.

## Session: position persistence, protective stop service, position manager loop

Continued from `4109c37` per the mission's next-step list ("Build
continuous position expectancy/persistence" / "PROTECTIVE STOP
EXECUTION" sections):

- Added `trade_stops_level`/`trade_freeze_level` to `SymbolSpec`
  (defaults 0, permissive for existing tests), populated by
  `Mt5Gateway._symbol_spec()`. **TESTED (live)**: real DEMO terminal
  reports `trade_stops_level=0`/`trade_freeze_level=0` for all three
  canonical symbols on this IC Markets account — no broker-side minimum-
  distance restriction here; the enforcement code path is still
  exercised by tests using a non-zero value.
- New `position_management/state_store.py` (migration
  `0012_position_management.sql`, schema now 12): durable
  `position_management_state`. `initial_monetary_risk`/`entry_regime`
  written once, never updated (directive section 21). `peak_r`
  monotonic and persisted — verified to survive a fresh `sqlite3.Connection`
  (the process-restart-equivalent test). Full exit-timeline tracking
  (`decision_r`/`fill_r`/giveback-at-decision/giveback-at-fill/expected-
  vs-realized slippage). 11 tests.
- New `execution/stop_modification.py`: the safe protective-stop service.
  Same two-round fresh-check pattern as `execution/close.py`. Reuses
  `adaptive_exit.resolve_new_stop_price()` (no duplicated monotonic-
  guarantee logic) — a stop that wouldn't advance protection sends
  nothing (`NO_CHANGE`). Refuses a too-close-to-price stop using the new
  `trade_stops_level` field. 15 tests, including a broker-already-
  advanced-between-check-and-send race.
- New `position_management/manager.py`: `review_position_once()` — the
  real per-position review cycle wiring `expectancy.py` → `adaptive_exit
  .py` → `state_store.py` → (`stop_modification.py` or `close.py`)
  together, exercised end to end against a real `FakeGateway` + real
  SQLite DB (not just isolated pure-function tests). Converts an
  adaptive-exit `new_stop_r` back to a real price via `entry_price +/-
  r * initial_stop_distance_price` (never re-derives risk distance from
  a moved stop). 10 end-to-end tests: HOLD, breakeven stop-advance (BUY
  and SELL), every FULL_CLOSE trigger (early-TP, thesis-invalidated,
  regime-reversed, max-holding-time), peak-R persisting across
  successive review calls, and the R-quarantine HOLD path. One test
  incidentally proved `review_position_once()`'s triggered reconciliation
  pass genuinely runs (traced through `FakeGateway`'s synthetic
  zero-timestamp closing deal falling outside the reconciliation window —
  a simulator artifact, not a code gap; documented precisely rather than
  asserted vaguely).
- Found and logged BUG_BACKLOG.md item 8: `review_position_once()`
  never calls `state_store.record_exit_fill()` after a close, so the
  exit-timeline's fill-side fields stay unset even on a successful
  close — needs a reconciliation-driven follow-up.

Full suite: 916 passed, 0 failed, 0 skipped (up from 880).

## Session: external-review safety checkpoint (17 findings), resumed from cbe16b3

Resumed per the mission prompt's explicit instruction to fix the latest
external review findings before any further PAPER/DEMO work. Inspected
`git status`/`git log`/`git diff` first (clean, `cbe16b3` HEAD, matching
the mission's "latest confirmed pushed commit") and reread CLAUDE.md,
MASTER_BUILD_DIRECTIVE.md, PROJECT_STATUS.md, WORKLOG.md, BUG_BACKLOG.md
before making any change, per the mission's "DO NOT RESTART" instructions.

All 17 findings from the external review of `cbe16b3` are now fixed —
full per-finding detail is in BUG_BACKLOG.md's "Fixed" section (search
"17 findings before PAPER/controlled-DEMO validation"); summary:

1-3. `execution/stop_modification.py` rewritten so BOTH rounds (before
   `order_check` and immediately before `order_send`) are identical:
   independent fresh DEMO/kill-switch-adjacent verification, fresh
   `symbol_info`/`symbol_info_tick` (trade mode, stops level, freeze
   level, execution-grade quote freshness), and the take-profit/stop
   values rebuilt from the FRESH round-2 position snapshot rather than
   reused from round 1. A changed request is `order_check`ed again before
   send. 21 tests (up from 12).
4. `position_management/manager.py` only records `request_at_utc` for
   `close.POST_SEND_STATUSES` (`SENT`/`REJECTED`/`UNKNOWN`) — a pre-send
   block (`NOT_DEMO`/`ALREADY_CLOSED`/`VOLUME_MISMATCH`/
   `BROKER_CONSTRAINT`) never fabricates a request timestamp.
5. `state_store.get_or_create_state()` raises `PositionStateConflictError`
   on a conflicting repeat call and validates `initial_monetary_risk` is
   positive/finite before ever writing a row.
6. An invalid initial risk now routes through a NEW degraded-health
   quarantine path (`position_risk_incidents` table, migration
   `0013_position_risk_quarantine`, schema now 13) — `record_risk_incident()`,
   idempotent per-position while unresolved — rather than an
   indistinguishable healthy HOLD.
7. `manager.py` now journals `POSITION_REVIEWED` on every review and
   `STOP_ADVANCED` on every real stop send (its own `position-review:
   {id}` chain); `POSITION_CLOSED` was already journaled by
   reconciliation recovery and now carries the full multi-deal aggregate
   (see 15 below).
8. `manager.py` now calls `state_store.record_exit_fill()` with REAL
   fill_r/broker_response_at_utc computed from the actual closing
   deal(s) reconciliation recovers — closes BUG_BACKLOG.md's previously
   open item 8.
9. A resolvable `PARTIAL` fill in `execution/service.py` now resolves its
   real broker position (same broker-history lookup the DONE path uses)
   and immediately persists a `positions` row scaled to the ACTUAL filled
   volume; an unresolvable one becomes `UNKNOWN`/`PENDING_RECONCILIATION`
   and blocks new entries via the same `UNKNOWN_OUTCOME` incident path
   every other unresolved case uses. Investigating this surfaced a
   genuine pre-existing gap: nothing in this codebase had EVER created a
   `positions` table row for a normal FILLED entry either — fixed too,
   via new `execution/store.create_local_position()` (idempotent on
   `broker_position_id`), used by both the FILLED and PARTIAL paths.
10-11. `portfolio/exposure.py`'s `compute_exposure()` now folds
   `pending_positions` into per-symbol/currency-direction/USD/
   positions-per-symbol heat, not just the total-risk sum (a resting
   order previously only counted toward the total ceiling, invisible to
   every other heat dimension). `correlated_cluster_exposure()`'s
   misleadingly-named local `open_symbols` var renamed
   `symbols_with_exposure`; `open_symbols` renamed `open_or_pending_symbols`
   across `core/final_permission.py`/`portfolio/correlation.py`.
12. `execution/service.py` gained `_verify_critical_broker_state()`:
   this execution boundary now directly re-fetches account_info/
   terminal_info (via `verify_demo_before_order`), the REAL persisted
   kill-switch state (`core.kill_switch.get_state()`), and
   symbol_info/symbol_info_tick itself, called independently before
   `order_check` and again immediately before `order_send` — never
   solely trusted from a caller-supplied `FreshEvidence`, which could
   return the same cached object on both calls.
13. A second `order_check()` of the SAME exact request, plus a fresh
   `margin_free` comparison against THIS module's own fresh
   `account_info()`, now runs immediately before every `order_send` — the
   authoritative final margin/broker recheck.
14. `execution/request_token.py`'s `request_token()` now derives an
   `ASN:<16-hex-char SHA-256 prefix>` token from the FULL
   `client_request_id` — the old `client_request_id[:16]` prefix slice
   could collide whenever two distinct ids shared a common prefix (a real
   risk for structured id schemes).
15. `execution/reconciliation.py` gained `find_closing_deals()` (plural):
   every OUT/INOUT/OUT_BY deal for a position, oldest first — not just
   the latest OUT. `_recover_missing_local_position()` now records EVERY
   deal, aggregates volume/commission/swap/profit across all of them for
   the journaled totals, and resolves each deal's local `order_id` from a
   REAL matching `orders` row (or NULL when unprovable) instead of
   falsely reusing the position's entry order id.
16. The position update, every deal insert, and the `POSITION_CLOSED`
   journal event for one reconciliation recovery are now ONE atomic
   transaction — `journal/events.py` gained `_append_event_locked()` (the
   same insert `append_event()` does, minus its own `BEGIN IMMEDIATE`, for
   a caller that already holds the write lock).
17. `PROJECT_STATUS.md`'s stale statements fixed: Phase 5 now correctly
   describes the existing continuous-expectancy engine and
   `manager.review_position_once()` loop core (only the outer scheduler
   is still pending); the hardcoded "Schema version 11"/"Schema at
   version 8" mentions replaced with pointers to the single authoritative
   "Schema version" section (now 13); the two "`order_send` still does
   not exist anywhere in this codebase" paragraphs (left over from before
   Phase 4 was built) rewritten to describe current reality.

Full suite: 961 passed, 0 failed, 0 skipped (up from 916). No secrets,
credentials, runtime DB, raw bars/ticks, logs, or model artifacts staged
for commit (verified via `git status`/`git diff` before committing).
