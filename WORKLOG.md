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

## Session: external-review safety checkpoint (16 new findings), resumed
## from the working tree ahead of 2bd1bf0

Resumed per the mission prompt's explicit instruction to preserve local
work newer than the last externally-reviewed push and fix a fresh batch
of 16 external-review findings (numbered NF1-NF16 to distinguish them
from the prior 17) before continuing into backtest/ML/PAPER/runtime
work. Backtest/walk-forward infra (`adaptive_scalper/backtest/`,
`adaptive_scalper/simulation/`, migration `0014_backtest`) that was
already in progress when this review landed was preserved untouched and
resumes after this checkpoint, per the mission's explicit "do not
discard local work" instruction.

Full per-finding detail is in BUG_BACKLOG.md's "Fixed" section (search
"16 new findings before resuming backtest/ML/PAPER work"); summary:

1. `execution/service.py` and `execution/stop_modification.py` both took
   an injectable `clock: Callable[[], float]` (default `time.time`)
   through to `validate_execution_quote()`, called independently at each
   verification round rather than sharing one `now` computed once at the
   top of a two-round check — closes a real window where a stale tick
   could survive into round 2 undetected.
2. `_verify_critical_broker_state()` now fetches `symbol_spec` fresh each
   round and independently checks `identity_matches_canonical()` (new
   public function in `gateway/symbol_validation.py`) and
   `validate_direction_for_new_exposure()` against it, rather than
   trusting the caller's `FinalPermissionInput.direction_check`/
   `asset_identity` fields alone.
3-4. `execution/close.py` rewritten to the same two-identical-rounds
   standard as `stop_modification.py`: fresh symbol/tick/filling-type
   each round, a second `order_check` before send when the final request
   differs from round 1's, and result classification that distinguishes
   `FULLY_CLOSED`/`PARTIAL_CLOSE`/`RESTING`/`CANCELLED`/`REJECTED`/
   `UNKNOWN` rather than collapsing everything non-rejected into `SENT`.
   A `PARTIAL_CLOSE` now calls `_apply_partial_close_to_local_state()`
   to update local volume/risk from the broker-confirmed filled volume.
5-6. `orders` gained durable typed fields (migration `0015_entry_fills`):
   `requested_monetary_risk`/`filled_volume`/
   `filled_initial_monetary_risk`/`remaining_volume`/
   `remaining_pending_monetary_risk`, written via new
   `execution/store.record_order_risk_accounting()` — a RESTING order's
   pending risk is now reconstructable from SQLite + broker truth alone,
   with no in-memory object required.
7-10. New `execution/entry_fills.py`: `record_entry_fills()` persists
   EVERY entry deal (new `deals` columns from the same migration:
   `fee`/`entry_type`/`deal_type`/`broker_order_ticket`/`magic`/
   `comment`) and recomputes the position's aggregate (weighted-average
   price across all matching deals) from them — safe to call again if
   more fills trickle in for the same `broker_position_id`, and refuses
   to mutate the aggregate once R-management is already active on that
   position (records an incident instead). `execution/
   position_resolution.py` gained `resolve_entry_fill_evidence()`,
   returning real broker execution evidence (deals, total filled volume,
   weighted-average price) so a fill's entry price is NEVER persisted as
   `result.price_filled or 0.0` — an unprovable fill stays
   UNKNOWN/PENDING_RECONCILIATION.
11. `execution/unknown.py`'s `resolve_unknown_order_without_broker_id()`
   now requires direction/volume/magic compatibility (new
   `_volume_compatible()`/`_magic_compatible()`) and, for deals/history
   orders, a tight time-window match (new
   `_within_time_window()`, default 300s) in EVERY evidence-source loop,
   not just token+symbol — a token match with the wrong direction/volume/
   time is excluded as evidence, not treated as a conflict.
12. `execution/reconciliation.py` gained `reconcile_pending_orders()`
   (mirrors `reconcile_positions()`, run against `orders_get()` vs local
   `SUBMITTED`/`ACCEPTED`/`PENDING`/`RESTING`/`PARTIAL`/`UNKNOWN` orders)
   and `_recover_missing_local_order()` (deferred-imports
   `entry_fills`/`position_resolution` to avoid a circular import). A
   dangerous unresolved pending-order mismatch now blocks new entries the
   same way an unresolved position mismatch does.
13-14. `position_management/manager.py`'s `_maybe_record_exit_fill()`
   rewritten to aggregate ALL authoritative closing deals for a position
   (not just the latest via `ORDER BY ... LIMIT 1`) for the real total
   net P/L, weighted-average exit price, and `fill_r`; now also computes
   and persists `realized_slippage` (decision reference price vs
   broker-authoritative weighted fill price, correct BUY/SELL sign).
15. `record_incident()` gained an optional `dedup_key` (falling back to
   `f"{incident_type}:order:{order_id}"` when an order id is known): a
   repeat call with the same key updates the existing unresolved row's
   `last_seen_at_utc`/`occurrence_count`/`detail` (migration
   `0017_incident_dedup`) instead of inserting a duplicate every
   reconciliation cycle. Both `reconciliation.py` call sites now pass an
   explicit key derived from `finding_type` + the broker position/order
   id the finding concerns.
16. `PROJECT_STATUS.md`'s stale Gateway Protocol paragraph (claiming
   `order_send`/`order_check`/`positions_get`/`orders_get` were
   deliberately excluded from the Protocol) rewritten to reflect that all
   four now exist and are tested; the hardcoded "Schema version 13"
   section updated to 17 (`0014_backtest` through `0017_incident_dedup`
   added); the "no order_send/order_check exists anywhere in this
   codebase" and "the directive §36 final-gate BLOCK_SYMBOL_NOT_ALLOWED
   check does not exist yet" paragraphs (both left over from before the
   17-findings checkpoint's execution work landed) rewritten to describe
   current reality — `order_send`/`order_check` exist and are fake-tested;
   `BLOCK_SYMBOL_NOT_ALLOWED` is implemented and composed into
   `core/final_permission.py`.

Full suite: 1024 passed, 0 failed, 0 skipped (up from 961). No secrets,
credentials, runtime DB, raw bars/ticks, logs, or model artifacts staged
for commit (verified via `git status`/`git diff` before committing).

## Session: backtest / walk-forward / OOS / Monte Carlo infrastructure
## (directive section 80), resumed per mission's "continue automatically"

Resumed the paused backtest work (`adaptive_scalper/backtest/engine.py`
was written but untested going into the NF1-16 checkpoint) per the
mission's explicit "do not stop after pushing" instruction.

First verified `engine.py`'s wiring against every production decision
core it calls (`compute_bar_features`, `classify_regime`/`RegimeTracker`,
`select_proposal`, `calculate_safe_volume`, `estimate_cost`,
`evaluate_position_expectancy`, `evaluate_adaptive_exit`/
`compute_current_r`/`resolve_new_stop_price`) by reading each real
signature and return type, then proved it end to end with new
`tests/test_backtest_engine.py` (10 tests) against deterministic
synthetic bar series (a clean uptrend/downtrend for a reliable
`momentum_continuation` fire, and a flat series to prove zero trades and
no crash): entries only in the trend's direction, every trade force-
closed by the end of the range, `final_equity` exactly reconciling
against the sum of realized trade P/L, a monotonically-increasing-in-
time equity curve, determinism (same input -> byte-identical output),
the `news_limitation_note` honesty check, and the `ValueError` on too few
bars. All 10 passed on the first run, giving real confidence the engine
was correctly wired, not just syntactically valid.

Then built the three pieces `run_backtest()` alone doesn't cover:

- `backtest/persistence.py`: `record_backtest_run()` — idempotent-on-
  `run_id` durable recording of a run's dataset (via `dataset.py`'s
  existing `build_dataset_snapshot()`/`record_dataset()`), dataset-usage,
  `backtest_runs` row, and every `backtest_trades` row (migration
  `0014_backtest`, already created but never actually written to before
  this). `run_backtest()` itself deliberately stays pure/DB-free;
  persistence is an explicit per-caller opt-in.
- `backtest/walk_forward.py`: `run_walk_forward()` — N sequential, non-
  overlapping (optionally `embargo_bars`-separated) folds, each an
  independent `run_backtest()` call walked forward in time, never
  shuffled (shuffling would leak a later fold's characteristics into an
  earlier fold's decisions, which directive section 80's "NO LOOKAHEAD"
  forbids). Named scope limit documented in the module docstring: each
  fold's feature engine warms up fresh at its own start rather than
  reaching into a prior fold's bars. Aggregate metrics pool every fold's
  trades in chronological order via `engine._compute_metrics()` (reused,
  not reimplemented).
- `backtest/oos.py`: `run_untouched_oos()` — the only sanctioned way to
  run genuine out-of-sample validation (directive section 65: "An OOS
  dataset that influenced design is no longer untouched"). Checks
  `dataset.has_dataset_been_used_as()` against the content-checksummed
  dataset BEFORE running anything; raises `DatasetContaminatedError` if
  the exact range was ever used for `TRAINING`/`VALIDATION`/
  `WALK_FORWARD_FOLD`, or already spent as `OOS` once before (unless
  `allow_oos_reuse=True` is passed explicitly — a deliberate, named
  escape hatch, not a silent default).
- `backtest/monte_carlo.py`: `run_monte_carlo()` — trade-ORDER
  resampling (random permutation of the REALIZED P/L multiset, never
  resampling-with-replacement, which would fabricate trade outcomes that
  never happened) over a completed run's trades. Deterministic given the
  same `seed` (`random.Random(seed)`, never hidden global RNG state).
  Reports final-equity/max-drawdown distribution stats and probability of
  ruin (equity ever touching `ruin_equity_fraction * initial_equity`).

New `tests/test_backtest_walk_forward.py` (9), `tests/test_backtest_oos.py`
(6), `tests/test_backtest_monte_carlo.py` (10), `tests/test_backtest_
persistence.py` (3) — 38 new tests total this session, covering: fold
ordering/non-overlap/embargo widening/too-few-bars rejection,
walk-forward persistence idempotency, OOS contamination refusal (by
prior TRAINING use, by prior WALK_FORWARD_FOLD use, by OOS-already-spent,
and the explicit reuse escape hatch), OOS non-interference across
genuinely different datasets, Monte Carlo determinism/seed-sensitivity/
sum-invariance-across-permutations/ruin-probability edge cases, and
dataset-row reuse (same bars, two different `used_for` purposes, one
dataset row) for direct persistence calls.

`adaptive_scalper/backtest/__init__.py` added (the package existed as an
implicit namespace package before this — now has a real docstring
summarizing every submodule's role, matching `simulation/__init__.py`'s
existing style).

PROJECT_STATUS.md gained a new "Backtest / walk-forward / OOS / Monte
Carlo infrastructure" section describing all of the above; the prior
"NOT yet done" paragraph's "backtest/walk-forward/OOS itself" line
removed now that it exists.

Full suite: 1062 passed, 0 failed, 0 skipped (up from 1024). No secrets,
credentials, runtime DB, raw bars/ticks, logs, or model artifacts staged
for commit (verified via `git status`/`git diff` before committing).

## Session: real ML observer training (directive sections 60-66)

Continued automatically per the mission's "do not stop" instruction into
the next pending task: implementing real ML training (previously
`learning/` had only the lifecycle/registry/promotion/drift machinery —
no code path produced a real trained artifact).

Wired causal feature capture into the backtest engine first, since real
training needs the EXACT feature vector a strategy used to decide an
entry, never a recomputation after the fact: `features/bar_features.py`
gained `NUMERIC_FEATURE_FIELDS`/`numeric_feature_vector()` (the
stationary, cross-time-comparable numeric subset of `FeatureSnapshot` --
deliberately excludes absolute price levels and the categorical `session`
string); `backtest/types.py`'s `SimulatedTrade` gained `entry_features`/
`entry_raw_confidence`; `backtest/engine.py` now captures the features
snapshot at the moment a signal is selected (`pending_entry_features`)
and threads it through `_OpenTrade` into the closed `SimulatedTrade`. New
`tests/test_backtest_engine.py::test_run_backtest_captures_entry_features_for_ml_training`
proves a real backtest trade's captured vector has real (non-None) values.

New `learning/dataset.py`: `build_training_rows()` assembles
`TrainingRow`s from real `SimulatedTrade`s — a trade missing captured
features, or with ANY `None` feature value, is EXCLUDED (returned
separately as `excluded_row_count`) rather than imputed.

New `learning/training.py`:
- `train_entry_outcome_model()` — fits `sklearn.linear_model
  .LogisticRegression` (directive section 64's suggested CPU-friendly,
  auditable family) to predict "probability of positive net outcome
  after costs". Uses a strictly TEMPORAL split (rows sorted by
  `entry_time_utc`; latest `validation_fraction` validates, never
  shuffled) with an optional `embargo_rows` purge gap (directive section
  65's "temporal/purged split"). Refuses below
  `DEFAULT_MIN_TRAINING_SAMPLES=200` (directive section 62) or when a
  split has only one outcome class. Reports validation accuracy/AUC/
  Brier-score (calibration).
- `save_model_artifact()`/`load_model_artifact()` — joblib serialize/
  deserialize with a SHA-256 checksum verified BEFORE deserializing
  (documented why `joblib.load()`'s pickle-based deserialization is safe
  here: exclusively this codebase's own prior training output, never an
  externally-supplied file).
- `register_entry_model()`/`train_and_register_entry_model_from_trades()`
  — registers a NEW model version via `learning.registry.register_model()`
  at `INSUFFICIENT_DATA` or `BASELINE`, **never** `CURRENT`. Module
  docstring states plainly this is STAGE 1 MODEL OBSERVER ONLY (directive
  section 61): zero execution/selector influence; promotion to `CURRENT`
  requires a separate, later `evaluate_promotion_gate()` pass this module
  does not attempt.

Caught and fixed a real cross-platform bug during testing: `model_key`
naturally contains `:` in this codebase's usual compound-key convention
(e.g. `entry_model:XAUUSD`), but `:` is a reserved Windows filename
character — `save_model_artifact()` raised `OSError: [Errno 22] Invalid
argument` building the artifact path from a raw `model_key`. Fixed with
`_safe_filename_component()` (replaces the full Windows-reserved
character set, not just `:`) applied only to the FILENAME, never the
`model_key` value stored in the registry. Regression test:
`test_register_entry_model_sanitizes_a_model_key_containing_colons_for_the_filename`.

New `tests/test_learning_dataset.py` (6 tests) and
`tests/test_learning_training.py` (15 tests, including a deterministic
separable synthetic task proving the model learns REAL structure --
validation accuracy/AUC both >0.9, not just "doesn't crash" -- and an
end-to-end test training against genuine `run_backtest()` output rather
than hand-built fixtures). `requirements.txt`'s scikit-learn comment
updated ("future ML models" -> the actual module that now uses it).

PROJECT_STATUS.md's "Model state / ML self-learning (observer stage)"
section rewritten to describe all of the above and explicitly restate the
STAGE 1 observer-only boundary.

Full suite: 1083 passed, 0 failed, 0 skipped (up from 1062). No secrets,
credentials, runtime DB, raw bars/ticks, logs, or model artifacts staged
for commit (verified via `git status`/`git diff` before committing).

## Session: PAPER engine (directive section 132)

Continued automatically per the mission's "do not stop" instruction into
the next pending task: PAPER mode ("real MT5 market data, simulated
fills, never a broker order_send").

Designed around reusing `backtest.engine.run_backtest()`'s exact
production decision cores rather than a parallel reimplementation
(directive: "Do not create decorative/showpiece subsystems disconnected
from the real decision path") — but `run_backtest()` was built for a
BOUNDED historical range (it force-closes any still-open trade at the
final bar so metrics are never computed over a truncated position),
which is wrong for PAPER: an ongoing process has no "end of range," and
force-closing every cycle would fabricate exits that never happened.

Extended `run_backtest()` with an incremental mode instead of forking a
second engine: new `OpenPositionState` (types.py) captures everything
needed to resume a still-open trade; `force_close_at_range_end: bool =
True` (default preserves existing bounded behavior exactly — verified by
the full existing test suite passing unchanged) and
`resume_open_position`/`resume_regime_tracker` let an incremental caller
pass a position back in. Documented the exact resume CONTRACT a caller
must uphold in the docstring: the bars array must be [trailing
feature_lookback bars of context] + [only genuinely NEW bars] -- never
the full accumulated history again, which would re-decide already-
processed bars against the resumed position's current (already-moved)
stop/target using stale price action.

Caught a real, non-trivial divergence bug while proving this correct: a
strict "does resuming in chunks match one continuous run" test initially
FAILED by a meaningful margin (~31 equity units on a 200-bar run).
Root cause: `regimes.classifier.RegimeTracker`'s hysteresis state
(confirmed/candidate/candidate_count) was rebuilt from `UNKNOWN` on every
`run_backtest()` call, with no way to resume it -- an incremental caller
restarting the tracker every cycle genuinely diverges from what a
continuously-running tracker would have decided, defeating directive
section 13's "do not flip on one noisy bar" guarantee across cycles.
Fixed: `RegimeTracker` gained `initial_candidate`/`initial_candidate_count`
constructor params and a `.state` property; `run_backtest()` gained
`resume_regime_tracker: RegimeTrackerState | None` and always returns
`BacktestResult.final_regime_tracker_state`. Proven with a dedicated
regression (`test_run_backtest_resume_regime_tracker_reproduces_
continuous_processing`) that explicitly shows WITHOUT resuming it the
two runs diverge, and WITH it they match exactly (trade-for-trade,
including the still-open position). New `RegimeTracker` unit tests in
`test_regime_classifier.py` cover `.state`/resumed-construction directly.

New `adaptive_scalper/paper/`:
- `state.py` — `paper_session_state`/`paper_trades` persistence
  (migration `0018_paper`), deliberately separate tables from
  `positions`/`orders`/`deals` (directive section 82: "Evidence classes
  must not silently receive identical weight" -- a simulated PAPER
  position must never be reachable by `execution/reconciliation.py`'s
  broker-truth recovery path). `record_paper_trades()` is idempotent
  (`INSERT OR IGNORE` on `(session_key, entry_time_utc, direction)`).
- `engine.py` — `run_paper_cycle()`, the only entry point. No `Gateway`
  parameter at all -- a future runtime-engine caller supplies `bars`
  (fetched live from the real MT5 terminal). Its whole job is correct
  windowing (`_slice_resume_window()`: computes exactly the bar slice
  the resume contract needs from whatever full bar history the caller
  supplies, so callers never have to get this right by hand -- the exact
  mistake caught mid-session in a badly-written test) and atomic state
  persistence (`BEGIN IMMEDIATE`/`COMMIT`/`ROLLBACK`, so a crash between
  "decide" and "persist" is always safely retryable via the idempotent
  trade recording).

`tests/test_paper_engine.py::test_run_paper_cycle_incremental_feeding_
matches_a_single_shot_backtest` is the strongest correctness proof: cycles
through a 220-bar range in growing chunks (50/100/150/200/220 bars fed
each time) and asserts the FINAL persisted state (equity, every trade,
the still-open position) is byte-identical to one non-incremental
`run_backtest()` call over the same full range.

Also fixed a false positive this work triggered in
`test_architecture_execution_boundary.py` (a docstring literally
contained the substring `.order_send(` while explaining that PAPER mode
never calls it) -- reworded without changing meaning; added `backtest`/
`paper` to that test's `FORBIDDEN_DIRS` documentation list for
completeness (the actual enforcement already covered them via the full
directory scan).

21 new tests (7 `test_paper_state.py`, 6 `test_paper_engine.py`, 5 new
`backtest.engine` resume/force-close tests, 3 new `RegimeTracker` tests).
Full suite: 1104 passed, 0 failed, 0 skipped (up from 1083). No secrets,
credentials, runtime DB, raw bars/ticks, logs, or model artifacts staged
for commit (verified via `git status`/`git diff` before committing).

## Session: PAPER/backtest correctness checkpoint (2026-09-24, cloud)

Cloud session (Linux, Python 3.13 venv; `MetaTrader5` is Windows-only
and was not installed; no live broker access attempted). Read
MASTER_BUILD_DIRECTIVE.md/PROJECT_STATUS.md/WORKLOG.md/BUG_BACKLOG.md
first. Baseline before any change: 1096 passed, 7 skipped
(`test_mt5_gateway_live.py`, no terminal), 1 failed
(`test_guardrails.py::test_outside_project_root`, Windows path
semantics on Linux -- BUG_BACKLOG item 12).

Wrote `tests/test_backtest_correctness_regressions.py` FIRST and
confirmed it failed against the unmodified code (24 failures), then
fixed. A scripted stub strategy is monkeypatched into
`backtest.engine.build_active_registry` so each defect triggers at an
exact bar with hand-computed prices; a seeded random-walk property test
exercises the real strategies.

Fixed in `backtest/engine.py` (restructured per-bar loop: fill prior
decisions at open -> features/regime on every bar -> SL/TP + review of
the open trade including the entry bar -> scan):
- pending entry / pending exit returned and resumable across calls;
- adaptive FULL_CLOSE fills at the next bar's open; range-end close at
  the last bar's close (`simulate_fill(..., at="close")`);
- entry bar fully managed; SL/TP trigger on bid/ask; gapped stops fill
  at the open less slippage; positions marked at bid/ask;
- monotonic `peak_r` from 0.0 (parity with live `state_store`);
- cost accounting: no double-counted entry friction, commission and
  per-rollover swap charged, `total_cost` = entry + exit friction +
  commission + swap, so `gross = net + total_cost` = mid-to-mid P/L.
Fixed in `paper/`: `pending_entry_json` (migration 0019), single-new-bar
cycles now run, short-history guard. Fixed in `backtest/oos.py`: overlap
(not exact-checksum) contamination check via
`dataset.find_overlapping_usage()`.

The property test also caught the one-new-bar PAPER lag, which none of
the named defects covered.

Two existing tests corrected, not weakened -- details in BUG_BACKLOG
"Fixed". New open items 10-13 recorded in BUG_BACKLOG (first-cycle deep
history labeled PAPER_LIVE_DATA; non-atomic OOS check/record; Linux run
of the Windows-path guardrail test; holding_seconds one bar short).

Full suite: 1138 passed, 7 skipped (live MT5 only), 1 failed (pre-
existing Windows-path guardrail test, unchanged). No secrets,
credentials, runtime DB, logs or model artifacts staged.

## Session: Checkpoint A -- Phase 0 simulation hardening (2026-09-24, cloud)

New directive ("FINAL COMPLETION, SAFETY HARDENING, CLOUD
IMPLEMENTATION...") received. Inspected git/PR state first: working tree
clean, `main` still 8e67f77, PR #1 (checkpoint 1, 856aa04) open. The
cloud session may only push `claude/pensive-newton-tckoid`, so every
further checkpoint lands there as its own commit and updates PR #1.

Phase 0 items already fixed in checkpoint 1 were re-proven by test
(0.1 pending entry, 0.2 one-new-bar, 0.3 causal exits, 0.4 entry-bar
SL/TP, 0.5 peak R); the rest implemented now:

- 0.7 deferred-entry revalidation and 0.8 historical risk halts
  (`backtest/engine.py::_revalidate_and_open`, `_risk_halt_reason`,
  `RiskState`, `BacktestConfig.risk_limits`/`max_entry_fill_delay_seconds`,
  `external_open_positions`/`correlation_matrix` for multi-symbol PAPER).
- 0.3/0.6/0.13 causal fill references, per-component costs, provenance
  (`SimulatedTrade` fields, `FILL_MODEL_VERSION`, `FillAssumptions.provenance`,
  `entry_evidence`), persisted via migration 0020 for both
  `backtest_trades` and `paper_trades`.
- 0.5 `peak_r_time_utc`/`last_current_r` on the resumable position.
- 0.9 OOS: provenance in contamination errors, boundary tests (exact,
  partial, nested, one shared bar both sides, adjacent both sides,
  different symbol/resolution/provenance), `OOS_ANALYSIS_REUSE` purpose
  that also spends a range (closed a loophole where an analysis run on a
  fresh range would not have counted as having looked at it).
- 0.10 `SEQUENTIAL_FIXED_CONFIG_EVALUATION` label; 0.11 Monte Carlo ->
  `path_stress.run_trade_order_path_stress()` (git mv, tests renamed
  and updated: terminal equity reported once).
- 0.12 PAPER config fingerprint (`backtest/fingerprint.py`), fail-closed
  `PaperSessionConfigMismatchError`; legacy session without history is
  bound, with history is refused.
- BUG_BACKLOG #10 fixed: PAPER starts now (first cycle decides only the
  latest bar). Five existing PAPER tests seeded accordingly; their
  assertions are unchanged.
- BUG_BACKLOG #12 fixed: guardrails `is_outside_project_root()`
  Windows-path/prefix bug (stricter everywhere); e2e hook tests pin
  `CLAUDE_PROJECT_DIR`.

New tests: `tests/test_simulation_phase0.py` (35), shared helpers
`tests/sim_helpers.py`, `chunk=1` added to the incremental-equivalence
property test. `python -m compileall adaptive_scalper`: OK. Full suite
(Linux, Python 3.13): 1177 passed, 7 skipped (live MT5 only), 0 failed.

## Session: Checkpoint B -- research validation layer + broker chaos harness (2026-09-24, cloud)

Phase 1: new `adaptive_scalper/research/` (splits: label intervals,
purge, embargo, purged K-fold, CPCV + paths; stats: PSR, expected max
Sharpe, DSR, PBO/CSCV; ledger: append-only `research_trials`, migration
0021). Own implementations from the published definitions instead of the
`purgedcv` package (no dependency/license to track); verified by 22
hand-constructed tests and an AST isolation check in both directions.

Phase 2: `tests/chaos_harness.py` (deterministic `ChaosGateway`, fault
plan per method/call, `SimulatedCrash` as BaseException) and
`tests/test_broker_chaos.py` (38 scenarios). The harness found two real
defects, fixed in the same checkpoint:
1. `order_send` exceptions escaped the entry/close/SLTP services; an
   entry order stayed SUBMITTED with no incident (new exposure NOT
   blocked). Now UNKNOWN + incident (entries) / UNKNOWN + reconciliation
   (closes); `order_check` exceptions block without sending.
2. No startup quarantine of SUBMITTED/ACCEPTED orders left by a dead
   process, and no code applied UNKNOWN resolutions. New
   `execution/recovery.py`.
Verified the tests bite: with the three exception fixes reverted, 7 fail.

compileall OK; full suite 1237 passed, 7 skipped (live MT5 only), 0 failed.

## Session: Checkpoint C -- runtime orchestrator (2026-09-24, cloud)

Phase 3. New `adaptive_scalper/runtime/` (engine, scheduler, demo, paper,
market_data, news_monitor, advisory, state, logging_setup),
`gateway/factory.py`, `learning/observer.py`, migration 0022, `[runtime]`
and `[costs.SYMBOL]` config sections, `run_backtest`/`run_paper_cycle`
`entry_block_reason`, `run_reconciliation(journal_clean=False)`.

Test harness: `tests/runtime_helpers.py` (`LiveMarketGateway` reveals
pre-generated bars as a fake clock advances; every call still goes through
the chaos fault plan). The first end-to-end DEMO run exposed a harness
bug (fills priced from a static tick -> price 0) and the system failed
SAFE on it: the order went UNKNOWN (no positive-price deal evidence) and
the resulting orphan blocked the other two symbols through reconciliation
in the same cycle. The runtime chaos test then exposed a real hardening
gap: a RAG object whose methods raise crashed startup, because the engine
trusted RAG's own never-raises contract -- every advisory call site is now
isolated (`safe_rag_record`, guarded rebuild).

Architecture audits added (`tests/test_runtime_architecture.py`): single
`Mt5Gateway()` construction site, order_send/order_check allow-lists,
kill-switch bootstrap/clear and `OperatorAuthority` operator-CLI-only, no
LIVE/REAL mode strings.

New backlog: #14 broker timestamp timezone (BLOCKED-ON-LOCAL-MT5), #15
saturated-confidence re-entry, #16 per-second POSITION_REVIEWED volume.

Full suite: 1266 passed, 7 skipped (live MT5 only), 0 failed.

## Session: Checkpoint D -- RAG ingestion + OKF knowledge layer (2026-09-24, cloud)

Phases 4-5.

RAG: migration 0023 (`rag_memories.source_key`/`origin`, partial unique
index, `rag_ingestion_state` watermarks); `rag/ingestion.py` maps journal
events, PAPER trades, execution incidents, completed research trials and
ERROR/CRITICAL runtime events onto the eight memory types (TRADE_SETUP is
enriched from `position_entry_context`); `store_memory` is idempotent by
source key. The engine runs it as `rag_ingest` (P3, 60s) and at startup;
the hot-path `safe_rag_record` calls in `runtime/demo.py` (TRADE_SETUP,
EXIT_DECISION) and `runtime/paper.py` (TRADE_RESULT) were removed -- the
journal already carries those facts, now ingested with provenance.

OKF: re-checked the spec before implementing -- v0.2 is still the latest;
the canonical repo moved to GoogleCloudPlatform/open-knowledge-format
(the knowledge-catalog copy is a frozen snapshot). New
`adaptive_scalper/knowledge/` (model, loader, validate, search, advisor,
benchmark) and the curated `knowledge/` bundle (21 concepts). PyYAML
6.0.3 pinned in requirements.txt (safe_load only). The architecture audit
caught a literal "live" string in the first draft of the control-key list
(renamed to `enable_live`). Benchmark and conclusion (keep TF-IDF) in
`docs/KNOWLEDGE_MEMORY.md`.

Full suite: 1316 passed, 7 skipped (live MT5 only), 0 failed.

## Session: Checkpoint E -- training job, model walk-forward, DEMO cost evidence (2026-09-24, cloud)

Phases 6-7. Found while building the job: `backtest_trades` never stored
the entry feature snapshot, so no persisted backtest could ever become
training data -- migration 0024 adds `entry_features_json` (older rows are
excluded, not imputed). The existing `cost_observations` table (0008) was
never wired and lacks fill-level fields; the new
`execution_cost_observations` table is written after each DEMO send and
completed by an off-hot-path sweep. `learning/model_walk_forward.py` +
`learning/jobs.py`: training never promotes (registration state checked
explicitly, promotion gate only reported). Test-writing slips corrected
(purge arithmetic, closed-interval OOS overlap) and a float-noise issue
fixed for real: "beats the base rate" now requires a >= 1% Brier skill.

Pre-existing pyflakes findings (20, all in untouched files, incl. a
harmless duplicated `RegimeTracker.confirmed_regime` property) are left
for the QA phase.

Full suite: 1345 passed, 7 skipped (live MT5 only), 0 failed.

## Session: Checkpoint F -- complete operator CLI + observer-only dashboard (2026-09-24, cloud)

Phases 8-9. `cli.py` became the `adaptive_scalper/cli/` package (system,
operator, data, runtime, research, learning, knowledge, dashboard) so no
single file owns ~35 commands; the operator-authority audit now pins
`cli/operator.py`. New `kill-switch bootstrap` (explicit operator id +
reason; cannot touch an ENGAGED switch). Found: the old `dashboard`
command connected the dashboard process to MT5 (a second terminal client
next to the runtime, contradicting "observer only") -- removed; the
dashboard now opens SQLite read-only and learns terminal state from the
runtime's heartbeat. Found: offline research had no way to get a
SymbolSpec without MT5 -- migration 0025 snapshots it. Research commands
run over stored history and record VALIDATION / OOS usage and trials, so
`oos` is genuinely one-shot from the CLI too (tested: overlap refused,
spent refused, analysis-reuse labelled).

Dashboard verified three ways: TestClient (REST + WebSocket), a real
uvicorn process (page, /api/panels, websockets client), and headless
Chromium (15 panels rendered, live feed, no JS errors).

Full suite: 1382 passed, 7 skipped (live MT5 only), 0 failed.

## Session: Checkpoint H -- docs, handoff, order_check probe, final cloud QA (2026-09-24, cloud)

Phases 11-14 (+ the tooling Phase 16 needs). New `order-check-probe`
command + `execution/order_check_probe.py` + migration 0026: one
never-sent DEMO `order_check` after fresh DEMO/permission/identity/quote/
direction/risk-safe-volume/filling verification, evidence recorded for
BUG_BACKLOG #5 (audit allow-list extended for `order_check` only; the
module has no send path, tested). Docs: README, docs/ARCHITECTURE,
SAFETY, RESEARCH_VALIDATION, WINDOWS_DEPLOYMENT, RELEASE, QA_REPORT, and
LOCAL_MT5_HANDOFF.md (steps A-Y + evidence-based acceptance criteria +
the list of cloud-unverifiable items). PROJECT_STATUS rewritten at the top
with the directive's status tags and a component matrix; stale sections
(operating modes, retired strategies, next task, tests, unverified,
backtest/PAPER "not yet wired") replaced with current truth; CLAUDE.md
"Current state" refreshed. OKF runbooks v2 (a wrong bootstrap flag in v1
corrected). QA found that no test proved runtime restart recovery -- added
`tests/test_runtime_restart.py` (DEMO position keeps being managed with no
re-submission; crash mid-submission -> UNKNOWN blocks all new exposure;
PAPER sessions resume exactly) instead of overstating the report.

QA: compileall OK; ruff F/E9 clean; bandit 0 high (9 triaged false
positives); pip-audit no known vulnerabilities (MetaTrader5 excluded,
Windows-only); secret scan fixtures only.

Full suite: 1440 passed, 7 skipped (live MT5 only), 0 failed.

## Session: Checkpoint I -- cloud-fixable backlog items (2026-09-24, cloud)

With all directive work that can run in the cloud done, fixed the open
BUG_BACKLOG items that need no MT5: #7 (reconciliation now translates broker
symbols through `symbol_mapping`; unmapped ones are labelled
`UNMAPPED:<name>`), #11 (OOS overlap check + usage reservation in one
`BEGIN IMMEDIATE` transaction, before the backtest; a two-connection race
test and a crash test both fail on the old code), #13 (holding time measured
to the review bar's close -- max-holding exits were one bar late in backtest
and PAPER; `EXIT_REVIEW_TIMING_VERSION` added to the PAPER fingerprint so an
old session halts rather than mixing timings; the new regression test fails
on the old code), #16 (unchanged HOLD reviews journaled at most every 60 s;
all actions/changes still journaled; state records every review).

#13 changed the synthetic trend fixture's trade cycle from 5 to 3 bars, so
three backtest-resume tests needed new split points to keep their
precondition (a trade open at the split). Assertions unchanged; the
resumed==continuous property was re-verified at every boundary 60-214 and
the chosen boundary keeps the "without resume it diverges" half meaningful.
The runtime restart test now asserts `last_review_at_utc` advances (a direct
proof of continued management) instead of counting journal rows.

Full suite: 1451 passed, 7 skipped (live MT5 only), 0 failed.


## Session: Windows validation, checkpoint W1 -- inspection, data protection, test-suite safety (2026-09-25, Windows laptop)

Branch `windows-validation` created from `cloud-review` @ `ba02cf5` (clean tree,
tracking `origin/claude/pensive-newton-tckoid`). Not merged into `main`.

Data protection:
- `data/adaptive_scalper.sqlite3` (218 MB, WAL mode, no -wal/-shm present, no
  process holding it) was at schema **8**, not 26: 1,504,038 bars, 2,222 broker
  account deals, 2,234 broker account orders, 393 decision chains.
- Backed up with SQLite's online backup API to
  `data/backups/adaptive_scalper.pre-windows-validation.20260924T200037Z.sqlite3`
  (integrity ok; `data/` is git-ignored).
- Migrations 9-26 audited: CREATE / ADD COLUMN only, no DROP/RENAME/DELETE.
  Rehearsed on a copy: only `schema_migrations` row count changed (8 -> 26),
  integrity ok, second run a no-op. Then applied to the real DB: schema 26,
  integrity ok.

Environment: Windows 11 Home 10.0.26200; `.venv` Python 3.13.15; SQLite
3.50.4; git 2.55. The venv lacked two new pins (`websockets==17.1`,
`PyYAML==6.0.3`); installed from `requirements.txt` only (no pip self-upgrade).
`pip check` clean; `MetaTrader5` 5.0.6180 imports. Two MT5 terminals are
installed (`MetaTrader 5`, `MetaTrader 5 IC Markets Global`).

Defect found and fixed (test safety): the first Windows run of the "offline"
suite **launched the live MT5 terminal** (terminal64 parent = the pytest
process). `tests/test_cli_commands.py::test_broker_commands_fail_cleanly_without_metatrader5`
skipped only if `mt5_gateway` had a module global `mt5`, which the lazy
`_import_mt5()` never creates, so on Windows it ran `symbols`, `reconcile`,
`history bootstrap` (and would have run `paper`, `demo`, `order-check-probe`)
against the DEMO terminal. The run was killed during `history bootstrap`; the
temp databases show 0 orders and 0 order_check probes (only read-only calls
ran; `paper`/`demo`/`order-check-probe` never started). Fix:
- `tests/conftest.py`: autouse fixture blocks `_import_mt5` in every test
  except `test_mt5_gateway_live.py` (the only MetaTrader5 import path).
- The broken skip removed, so the fail-closed CLI behaviour is now verified on
  Windows too.
- `test_mt5_gateway_live.py` is opt-in (`ASN_LIVE_MT5=1`) because its
  collection-time `initialize()` launches the terminal;
  `scripts/windows_verify.ps1` sets it for its live step only.
- Regression tests: `tests/test_offline_mt5_guard.py`.

Windows compatibility: `test_oversized_and_symlinked_files_are_refused` failed
with WinError 1314 (non-admin users cannot create symlinks). Split into
`test_oversized_files_are_refused` (always runs) and
`test_symlinked_files_are_refused` (skips with the OS error only when the
symlink cannot be created). No assertion weakened.

Full suite on Windows before the symlink split:
`.venv\Scripts\python.exe -m pytest -q -rs` -> 1453 passed, 1 failed (symlink
privilege), 7 skipped (live MT5, opt-in), 278 s; no terminal process spawned.

Read-only broker facts (scratch script, no order_check/order_send; login
masked), terminal launched by the defect above and still running:
- account trade mode DEMO, `ICMarketsSC-Demo`, company Raw Trading Ltd, USD,
  hedging margin mode; account trade_allowed, trade_expert true; build 6191.
- **Terminal Algo Trading is already ENABLED** (terminal trade_allowed=true).
  Not enabled by this session. PAPER does not use it; kill switch is
  UNINITIALIZED so the DEMO runtime would block all new entries.
- 0 positions, 0 pending orders.
- XAUUSD 'Gold vs US Dollar', GBPJPY 'Great Britain Pound vs Japanese Yen',
  BTCUSD 'Bitcoin (USD)': exact-name matches; vol_min 0.01, step 0.01; stops
  and freeze level 0; filling_mode 2; `cli symbols` resolved and captured all
  three.
- **BUG_BACKLOG #14 confirmed:** `tick.time` is +10800 s (UTC+3) ahead of the
  real UTC clock on all three symbols. Stored bars are server time labelled
  `ts_utc` (FX history ends Friday 23:55 "UTC"; the FX close is 21:00 UTC).

## Session: Windows validation, checkpoint W2 -- broker server time (BUG_BACKLOG #14) (2026-09-25)

Measured: IC Markets DEMO `tick.time` = UTC + 10800 s on XAUUSD/GBPJPY/BTCUSD
(2026-09-24 20:12 UTC, US DST in effect); stored bars were server time labelled
`ts_utc`. News times are parsed offset-aware (true UTC), so bar-vs-news windows
were 3 h apart and every DEMO entry would have failed `execution_future_timestamp`.

Fix (central, at the gateway boundary; details in BUG_BACKLOG #14):
`gateway/server_time.py` (`[mt5] server_time_rule`, "UTC+2/US_DST" in
`config/default.toml`); `Mt5Gateway` converts ticks, bars, deals and orders to
UTC and range inputs to server time; `create_live_gateway(rule)` takes the rule
explicitly; startup refuses quotes in the future under the rule
(`SERVER_CLOCK_MISMATCH`) and records `server_clock` state; `doctor` prints the
per-symbol verdict; migration 27 marks pre-existing MT5 rows
`SERVER_UNCONVERTED` and the runtime/history/research commands refuse them until
`history convert-server-time` (backup first, one transaction, one shot);
`BAR_TIME_BASIS` joins the PAPER/backtest fingerprint.

A test caught an overstated claim: at the autumn fall-back two UTC hours share
one server hour, so UTC->server->UTC is not exact there (the broker's own data
merges that hour). Server->UTC is still one-to-one, which keeps bar keys unique;
the test now states that property.

Tests: new `test_server_time.py`, `test_mt5_gateway_time_conversion.py`,
`test_time_basis.py`, 3 engine startup tests in `test_runtime.py`, a fingerprint
test, and a live test. Full suite (Windows): 1481 passed, 9 skipped (8 opt-in
live MT5, 1 symlink privilege), 0 failed; fingerprint-affected modules re-run
after the last edit: 115 passed. Live (`ASN_LIVE_MT5=1`): 8 passed, including
`test_configured_server_time_rule_matches_live_quotes`.

## Session: Windows validation, checkpoint W3 -- real-data conversion, order_check, read-only ops (2026-09-25)

Winter half of the server clock verified from the broker's own history before
converting: the last M15 bar of each FX week was Friday 23:45 server time in
all 212 GBPJPY weeks (140 US-DST, 72 winter); a fixed UTC+3 clock would give
Saturday 00:45 in winter.

First real conversion attempt hit a UNIQUE collision and rolled back cleanly
(DB verified unchanged): 4 BTCUSD M15 bars were stamped at server 09:00/09:15
on spring-forward Sundays (2024-03-10, 2025-03-09, 2026-03-08), an hour the
server clock skips. Fix: `server_time.is_skipped_server_time`; migration 28
adds `bars_unconvertible` / `ticks_unconvertible`; the conversion MOVES such
rows there with their original values (never deletes or guesses); the gateway
drops them with a logged warning. Tests added for all three.

Real DB converted (`history convert-server-time`, own backup
`data/backups/adaptive_scalper.pre-server-time-conversion.20260925T025015Z.sqlite3`,
49 s): 1,504,028 bars, 2,234 broker orders, 2,222 deals, 15 jobs, 15 coverage
rows; 10 skipped-hour bars quarantined. Check: FX week now ends 21:00 UTC (US
DST) / 22:00 UTC (winter). `doctor: OK`, schema 28, integrity ok, clocks
VERIFIED on all three symbols.

Read-only ops: `status`/`health` TRADING_BLOCKED (kill switch UNINITIALIZED,
correct); `strategies` = the six active, both retired listed as retired;
`reconcile` CLEAN; `news status`: financecalendar UNAVAILABLE by design,
forexfactory HEALTHY (cache from 2026-09-19).

`order-check-probe --symbol XAUUSD --direction BUY`: retcode 0 "Done", margin
0.86, NOT sent; 0 positions / 0 orders after. BUG_BACKLOG #5 confirmed.

Full suite (Windows): 1485 passed, 9 skipped (8 opt-in live, 1 symlink), 0 failed.

## Session: Windows validation, checkpoint W4 -- live PAPER, dashboard, cost evidence (2026-09-25)

Live PAPER (CLI equivalent of START PAPER.bat, hidden console, output in
`logs/paper_stdout.txt`) started 03:10:11 UTC: ICMarketsSC-Demo, DEMO account,
three symbols resolved, `server_clock` VERIFIED, news HEALTHY, entry cycle every
4 s with 0 failures. Kill switch UNINITIALIZED -> `BLOCK_KILL_SWITCH` on every
cycle (correct; PAPER still processes bars). The 03:10 bar was processed at
03:14:59-03:15:03 (session cursors advanced to 03:10; 03:15 correctly treated as
still forming).

Dashboard (CLI equivalent of START DASHBOARD.bat): listens on 127.0.0.1:8765
only; all 15 panels status OK from the real DB (NO data shown as empty lists,
not invented); WebSocket pushes all 15 panels every ~4 s; `/api/panels` polling
works. Isolation: dashboard killed -> PAPER stayed RUNNING (heartbeat 1 s, 0
failures) -> dashboard restarted independently. Cosmetic finding: panel ages can
read -1/-2 s (one `now` taken before the panels are read while the runtime keeps
writing) -- BUG_BACKLOG #23.

Execution costs configured from this DEMO account's own history (2,222 deals,
2026-09-11..18): commission XAUUSD 3.514 / GBPJPY 3.501 / BTCUSD 0.00 USD per
lot per side; adverse slippage (fill vs requested, market orders) p90 XAUUSD 0.41
(n=196), BTCUSD 11.97 (n=141); GBPJPY n=7 is too thin, so its slippage stays
UNKNOWN and GBPJPY DEMO entries keep blocking with BLOCK_COST. Swap 0.0 (all
intraday). XAUUSD and BTCUSD -> BROKER_DEMO_CONFIRMED.

Because fill assumptions are part of the PAPER fingerprint, `paper_session_tag`
moved v1 -> v2 (the v1 sessions were 10 minutes old with no trades). Writing it
with PowerShell 5.1 `Set-Content -Encoding utf8` added a BOM that tomllib
rejects; `test_load_config_reads_the_shipped_default_toml` caught it; BOM removed.

Graceful stop verified (real Ctrl+C to the PAPER console): "stopping (Ctrl+C): no
positions are closed", engine state STOPPED, ENGINE_STOPPED event. Restarted at
03:21:18 UTC on the new config: RUNNING, 0 failures, no ERROR/CRITICAL events.

Full suite (Windows): 1485 passed, 9 skipped, 0 failed.

## Session: Windows validation W5 -- dashboard integration, risk ceilings, scheduler (2026-09-25)

Working branch: local `dashboard-review` tracking `origin/dashboard/responsive-live-windows`
(PR #2, base `windows-validation`); it already contained all of windows-validation
plus 12 dashboard commits; no new migrations (schema 28). Backup before running
the new code: `data/backups/adaptive_scalper.pre-dashboard-integration.20260925T092146Z.sqlite3`.

PAPER evidence from the pre-dashboard build, running since 03:21 UTC: ~6 h, 4,932
entry cycles, 0 failures, 17 news refreshes, 0 advisory failures. Kill switch
still UNINITIALIZED (no operator bootstrap yet), so -- by design (directive
section 45, backtest/engine.py) -- PAPER does not evaluate strategies. Read-only
`scan --source mt5` evaluated the six strategies on the live 09:15 bars: BTCUSD
momentum_continuation BUY 0.39 (TRENDING_UP), XAUUSD microstructure_acceleration
SELL 0.42 (RANGE), GBPJPY FLAT (ERRATIC).

Defects fixed:
1. Risk ceilings: `RiskConfig` accepted up to 20 % per trade/day/drawdown and any
   number of positions; nothing downstream clamped. Now `HARD_RISK_CEILINGS` in
   `config/constants.py` is enforced by `RiskConfig` AND `RiskLimits.__post_init__`
   (every construction path). `test_risk_limits_from_config_copies_every_field`
   used above-ceiling values only to prove copying; now uses distinct in-ceiling
   values. `test_risk_rejects_per_trade_exceeding_total_open_risk` would have
   passed via the new ceiling check, so it now uses in-ceiling values that only
   the per-trade <= total rule rejects.
2. Scheduler starvation: the scheduled news refresh did its HTTP fetch (httpx
   timeout 10 s per phase) on the single scheduler thread, delaying the 1 s DEMO
   position cycle by the whole fetch. Now `fetch_live` (network) runs on a worker
   thread, the task waits at most `NEWS_FETCH_BUDGET_SECONDS = 2.0`, a 1 s
   `news_poll` task applies the result on the scheduler thread (all SQLite stays
   there), one fetch in flight at most, `NEWS_FETCH_OVERDUE` warning after 60 s.
   The scheduler now records per-task last/max lag and max duration.
   Measured on the 6 h live run: news refresh 0.80 s, entry cycle 0.08 s, RAG
   rebuild 0.002 s, RAG ingest 0.0004 s.
3. The dashboard branch added a `"LIVE"` string (quote freshness), failing the
   safety audit `test_there_is_no_live_or_real_execution_mode`. The panel status is
   now FRESH/STALE; the browser shows "LIVE FEED" for a fresh quote.

Tests: `tests/test_runtime_scheduling.py` (lag metrics; a hanging calendar fetch
never starves DEMO position management -- fails on the old synchronous code),
risk-ceiling tests in `tests/test_config.py`. Full suite before fix 3: 1500
passed, 1 failed (the audit), 9 skipped; after: audit + dashboard tests 24 passed.

## Session: Windows validation W6 -- dashboard performance, honesty, screen fit, launchers (2026-09-25)

Dashboard defects found with a real Chromium (Playwright) against the live PAPER
runtime and the real 218 MB database:
1. Overview panel ran a full `PRAGMA integrity_check` on every refresh: 6.1 s per
   `/api/panels`, so the 2 s WebSocket feed fell back to polling and ages read
   -4..-6 s. Now `dashboard/health.IntegrityMonitor` checks off-thread at most
   every 600 s (own read-only connection; PENDING until the first result, never
   reported as "ok" or as a failure); `compute_all` pins one read snapshot before
   taking `now`. Measured after: 0.04-0.06 s per refresh, ages >= 0. The CLI
   `health`/`doctor` keep their synchronous full check.
2. With the dashboard server stopped, the page kept showing "Runtime: RUNNING" and
   the equity as current. Now, when the dashboard's own data is > 15 s old or the
   feed is offline: runtime/health "UNKNOWN", broker figures "—", kill switch
   "(last known)", banner with the data age. Verified: stop server -> after 20 s
   all of that shown; restart -> WebSocket reconnected by itself ("Dashboard: live").
3. Required summary items added: account balance, MT5 & account (connection +
   trade mode, stale-aware), total risk (% of fresh equity vs the 0.75 % ceiling).
4. At 911x512 (1366x768 at 150 %) the sidebar had its own horizontal scrollbar
   (brand name overflow): sidebar overflow-x hidden, brand wraps at a word break.
5. favicon 404 on every load: inline empty icon.

Screen-fit matrix (CSS px = physical / scale; all 7 views at each size): 1366x768,
1600x900, 1920x1080, 2560x1440, 1093x614 (1366@125), 911x512 (1366@150), 1280x720
(1600@125, 1920@150), 1536x864 (1920@125), 1707x960 (2560@150). Every size: no
page-level horizontal scroll, no header/content overlap, no sidebar overflow, no
table escaping its container, all nav buttons reachable, minimum font 11 px,
panels per view 6,4,5,5,6,5,17. Keyboard: nav buttons are <button>s with a 2.4 px
focus outline; Enter switches views. Light theme checked. Limitation: Chromium
viewport emulation of scaling, not the Windows display-scaling setting itself.

Launchers: `START PAPER + DASHBOARD.bat` and `START DEMO + DASHBOARD.bat` run
`doctor` first (stop with a visible error if it fails), DEMO also runs read-only
`reconcile`; both show kill-switch status (never change it), open the dashboard in
its own window and run the one runtime in the foreground (Ctrl+C). Added to every
launcher audit in `tests/test_launchers.py` plus a dedicated test.

Full suite (Windows, before the launcher change): 1505 passed, 9 skipped, 0
failed; launcher tests after: 50 passed.

## Session: Windows validation W7 -- verification, research, documentation (2026-09-25)

- `scripts\windows_verify.ps1`: RESULT PASS (`logs\windows_verify_20260925_135054.txt`):
  full suite 1514 passed / 9 skipped, live MT5 DEMO tests 8 passed.
- ruff (F, E9) clean; bandit 0 high / 7 medium (triaged false positives, one new B608 in
  `history/time_basis.py`, constant identifiers) / 3 low; pip-audit incl. MetaTrader5: no
  known vulnerabilities (BUG_BACKLOG #17 resolved). Tools in a throwaway venv.
- Scheduler on live PAPER (new build, 21 min): max duration 0.94 s (news fetch, inside the
  2 s budget), max lag 0.87 s, 0 failures.
- PAPER restart replay on real June 2026 XAUUSD bars (script kept outside the repo; output
  `logs\paper_restart_replay_XAUUSD.txt`): 5,975 / 855 / 121 restarts (every 1 / 7 / 50
  bars), 102 resumed with an open position, 96 with a pending entry: identical 96 trades,
  0 duplicates, identical equity 9,496.92, cursor on the last bar.
- Research on broker history. OOS holdout 2026-07-01..2026-09-18 reserved BEFORE any run and
  never touched. Backtests (design window): XAUUSD -514 (72 trades, PF 0.39), GBPJPY -503
  (183, PF 0.70), BTCUSD -423 (119, PF 0.62); each hit the 5 % drawdown halt within 2-4 days;
  `microstructure_acceleration` dominated. Walk-forward 5 folds each: all 15 folds -423..-518
  (halted), PF 0.46 / 0.56 / 0.47. Purged CV PSR(SR>0) 0.003 / 0.028 / 0.023. Path stress
  p95 max drawdown 603 / 696 / 578, ruin probability 0. Strategies NOT changed (directive:
  no tuning on samples); reported as a DEMO-readiness concern.
- Docs updated: PROJECT_STATUS (Windows matrix), docs/QA_REPORT (Windows section first,
  cloud report marked historical), docs/DASHBOARD, docs/WINDOWS_DEPLOYMENT (combined
  launchers, WAL-safe backup), docs/RELEASE, docs/SAFETY guarantee 5, LOCAL_MT5_HANDOFF
  status, BUG_BACKLOG #17/#23, CLAUDE.md current state.

## Session: Windows validation W8 -- release (2026-09-25)

- `scripts\build_release.ps1 -Version 0.1.0` at commit `e9e599c`: suite 1514 passed / 9
  skipped inside the build, OKF valid, `dist\AdaptiveScalperNext-0.1.0.zip` (391 entries,
  sha256 cc82c19c969fa6c4518fb173cb8d5fe8fb7030371f3f7a69087e5c5f84a48ebd) + manifest.
- Independent zip audit: no databases, logs, data/, .venv, dist, screenshots, keys, .env or
  credentials; secret-pattern hits only in the synthetic detector fixtures of
  `tests/test_knowledge.py` / `tests/test_guardrails.py`; all 8 launchers, 28 migrations and
  the dashboard present.
- `scripts\release_smoke_test.ps1`: PASSED. Its throwaway config has no `[mt5]` rule, so its
  informational `doctor` reported the clock MISMATCH (+10799 s) and "the runtime will refuse
  to start" -- the fail-closed check working as intended.
- Source release only; no frozen executable was built.
## Session: Deep audit and non-mutating preflight (2026-09-25)

Created branch `codex/deep-audit-20260925` from the integrated local dashboard
line after fetching all refs. Preserved concurrent Windows-validation commits;
did not merge to main. Backed up the active WAL database with SQLite's online
backup API to `data/backups/adaptive_scalper.pre-deep-audit.20260925T095839Z.sqlite3`;
source and backup integrity both `ok`.

Initial offline suite: 1514 passed, 9 skipped in 260.97 s. Final suite after
the preflight and smoke-isolation fixes: 1518 passed, 9 skipped in 115.05 s. `pip check` clean;
isolated pip-audit found no known vulnerabilities. Live doctor: DEMO, schema 28,
UTC storage and all three server clocks VERIFIED. Chrome validated the integrated
17-panel dashboard at the four required resolutions plus scaling-equivalent
viewports: no page overflow, sidebar overflow or escaped panels.

Implemented `preflight`, a read-only diagnostic that never migrates, reconciles,
checks/sends orders or changes safety state. Live result: READY_FOR_PAPER. DEMO
blockers are kill switch UNINITIALIZED, no fresh CLEAN reconciliation snapshot
in PAPER, and unknown GBPJPY slippage. Added three regression tests and the seven
required audit/operations documents. No `order_send` was performed.

The first 0.1.1 release smoke attempt exposed that its pre-pytest `doctor`
initialized the live terminal (read-only) despite the script's isolation claim,
and its nonzero result was ignored. Added the `ASN_DISABLE_MT5=1` import-boundary
guard, made doctor failure fatal, and added a fresh-process regression. No order
check or order send occurred.

## Session: PAPER -> DEMO transition, pre-start (2026-09-25 ~17:45 UTC)

- PAPER (PIDs 23564/25820, own console) stopped with its documented mechanism: a
  CTRL_C_EVENT delivered to that console only (dashboard runs on a separate console and
  was left running). stdout: "stopping (Ctrl+C): no positions are closed by stopping the
  runtime"; engine state STOPPED. No paper positions/trades existed (kill switch was
  ENGAGED by the STOP TRADING launcher at 17:38 UTC, so PAPER never entered).
- WAL-safe online backup: `data/backups/pre_demo_20260925T214640.sqlite3` (275 MB);
  integrity ok + 0 FK violations on live and backup; 45 tables; counts identical.
- `doctor`: schema 28, integrity ok, DEMO, all three server clocks VERIFIED.
- MT5 (read-only): ICMarketsSC-Demo, trade_mode DEMO, balance/equity 9707.85 USD,
  Algo Trading + account/expert trading allowed, 0 positions, 0 orders.
- `reconcile`: CLEAN. `order-check-probe XAUUSD BUY`: retcode 0 "Done", NOT sent.
- `preflight`: READY_FOR_PAPER; DEMO blockers = kill switch ENGAGED (operator), no fresh
  runtime reconciliation snapshot (only a running DEMO runtime writes it; CLI reconcile
  CLEAN), GBPJPY slippage UNKNOWN (runtime blocks GBPJPY with BLOCK_COST; left UNKNOWN).
- Kill switch NOT touched. DEMO NOT started: awaiting operator action.
- Full suite on Windows: 1518 passed, 9 skipped in 132 s.

## Session: Deep audit finalization, DEMO observation and release 0.1.3 (2026-09-26)

- A separate operator session subsequently disengaged the kill switch and started DEMO. The
  audit did not change that state, stop/start the runtime, cancel an order or send an order.
- Read-only broker snapshot: connected DEMO, permissions enabled, 0 positions, 0 pending
  orders. Local history contained 15 naturally filled and closed DEMO positions; recorded
  deal totals were gross profit -22.97 and commission -1.62.
- Found and reproduced ASN-012: stable reconciliation incidents were never resolved after a
  later complete broker snapshot no longer contained the finding. The live CLEAN snapshot
  and empty broker truth contradicted one stale open orphan-order incident.
- Fixed ASN-012 by resolving only absent, namespaced reconciliation finding keys. UNKNOWN
  and unscoped/manual incidents remain untouched. Focused safety/runtime set: 114 passed.
- Final source suite: 1521 passed, 9 skipped, 2 third-party warnings in 113.35 s.
- Built `dist/AdaptiveScalperNext-0.1.3.zip` from commit
  `e3bb2cca11422ed873dd32656dc2dd98ea6384fd`: 400 entries, SHA-256
  `84c25619702fee90a6a95444b655130b0fd86d6308cc7b509222bc42b630f352`.
  Build gate: 1521 passed / 9 skipped in 110.78 s.
- Clean extracted release smoke: PASS; MT5 disabled at the import boundary; packaged suite
  1521 passed / 9 skipped in 117.46 s. REAL-money execution remains disabled.
- Final source preflight remained `READY_FOR_PAPER`, not `READY_FOR_DEMO`. The running process
  predates ASN-012 and GBPJPY cost/quote evidence remains incomplete. Apply the source fix by
  a controlled flat restart; do not treat a running DEMO process as readiness proof.

## Session: Strategy Lab attribution, audit fixes, release 0.2.0 (2026-09-26, 15:00-19:40 GMT+4)

- Pre-work (read-only): DEMO runtime PIDs 25208/30560 (`cli demo`, started 14:49 from `bac6145`) and
  dashboard PIDs 30896/31324 running. MT5 DEMO account (ICMarketsSC-Demo, trade_mode 0), balance =
  equity 9,652.33 USD, 0 positions, 0 orders. Kill switch DISENGAGED, reconciliation CLEAN, heartbeat
  fresh, scheduler lag < 0.06 s. Preflight READY_FOR_PAPER; DEMO blocker: XAUUSD quote stale (Saturday).
  The runtime was not stopped, restarted or modified.
- Branch `feature/strategy-lab-attribution` from `bac6145`. Online backup
  `data/backups/pre_strategy_lab_attribution_20260926T110252Z.sqlite3` (quick_check ok on source and
  backup, 0 FK violations, schema 28).
- Built `dashboard/strategy_lab.py` + `/api/strategy-lab/*` (GET, on demand) + Strategy Lab view.
  Reconciliation on the backup: 2,272 deals, ledger = SQL = balance 9,652.33 USD.
- Found and fixed ASN-013 (legacy chains inflated the funnel/registry) and ASN-014 (close magic 0).
  Fixed ASN-008/009/010. Migration 0029 (deal reason + indexes) tested only on temporary databases;
  the production database is still at schema 28.
- Research (§16): walk-forward records show `microstructure_acceleration` = 91.5 % of trades, gross
  +0.005 R/trade versus costs 0.227 R/trade (docs/RESEARCH_VALIDATION.md §8). No tuning; OOS untouched.
- Tests: 28 new (tests/test_strategy_lab_attribution.py); full suite 1,557 passed, 9 skipped.
  pip check clean; pip-audit: no known vulnerabilities; bandit: no new findings.
- Browser: test dashboard on 127.0.0.1:8766 against a scratch DB copy; 8 viewports verified.
- Incident: an inline Python edit used the Windows default encoding and truncated
  `dashboard/page.py`. It was restored from HEAD and re-applied with explicit UTF-8 before commit.
- Cleanup manifest: docs/CLEANUP_MANIFEST.md (nothing removed; approvals pending).
- Release: `scripts\build_release.ps1 -Version 0.2.0` at `5f59280` (gate 1557 passed / 9 skipped),
  `dist\AdaptiveScalperNext-0.2.0.zip`, 406 entries, sha256
  a0945338a28d7b3cb6e025b018544725e5feae0daa9b09ced1c94b349062d06e. Independent zip audit: no databases,
  WAL files, logs, keys, .env or credentials; 29 migrations and all Strategy Lab files present.
  `scripts\release_smoke_test.ps1`: PASSED (MT5 disabled; packaged suite 1557 passed / 9 skipped).
  Source release only; no frozen executable built. (An earlier 0.2.0 build from `ef7290e` was discarded
  after the DEMO login number was found in three new doc lines; those lines were redacted first.)
- Final read-only check (~19:55 GMT+4): runtime RUNNING DEMO, heartbeat fresh, 0 task failures. Broker:
  1 open DEMO position (BTCUSD SELL 0.52, magic 240924, broker SL/TP set, initial risk 24.17 USD =
  0.25 %), locally tracked with a runtime entry chain (`microstructure_acceleration`). Reconciliation
  CLEAN, 0 unresolved incidents. Production database still at schema 28 (migration 0029 not applied).

## Session: Multiple simultaneous trades — root cause and fix (2026-09-26, 20:31-21:15 GMT+4)

- Pre-work (read-only): DEMO runtime PIDs 25288/10512 (`cli demo`) and dashboard 14524/27832 started
  20:30:54 GMT+4 from the main checkout at `7f604ab` (release 0.2.0); migration 0029 applied 16:30:49Z,
  production schema 29. Kill switch DISENGAGED, reconciliation CLEAN, heartbeat fresh, 0 task failures.
  Running config: symbols XAUUSD+BTCUSD (GBPJPY excluded, ASN-007), 0.25 % / 0.75 % / 2 positions / 1 per
  symbol, M5. Runtime not stopped, restarted or modified; all development in worktree
  `.worktrees/multi-position`, branch `fix/multi-position-readiness`, temporary databases only.
- Evidence (production DB, read-only): 31 runtime DEMO positions since 2026-09-25 18:14 UTC. Broker deals
  prove two simultaneous positions already occurred: XAUUSD SELL (deal 1580733584, 19:25:00 -> SL 19:30:56)
  overlapped BTCUSD SELL (deal 1580738053, in 19:30:00) by 56 s. XAUUSD decisions end 2026-09-25 20:55 UTC
  (Friday close); every position since is BTCUSD. Last 24 h: 0 correlation, aggregate-risk, portfolio or
  max-position blocks; 34 final-permission blocks, all re-entry rules (confidence, same-direction
  improvement, 60 s cooldown); global blocks = kill switch (operator restarts), MT5 disconnects, 4 transient
  reconciliation. Each runtime started on Saturday (04:32, 10:49, 16:30 UTC) logged SYMBOL_EXCLUDED XAUUSD
  `stale_quote` and kept only BTCUSD.
- Root cause of single-position behaviour: expected -- XAUUSD market closed for the weekend, GBPJPY disabled,
  BTCUSD max one position. Defect found (ASN-016): the startup exclusion is permanent for the process, so the
  running runtime would never trade XAUUSD after Monday's open. Latent defect (ASN-017): working orders on a
  symbol without a position were not counted in max_open_positions. Both reproduced with failing tests, fixed.
- Added diagnostics: `describe_correlation_pairs`, runtime state `multi_position_readiness` and
  `symbol_admission`, dashboard panel MULTI-POSITION READINESS (Strategy Lab / Safety / All).
- Tests: `tests/test_multi_position.py`, 28 tests (two-symbol fills, same-symbol block, third position,
  fresh risk/margin per submission, high/missing/low correlation, per-instrument cost block, rejected order
  isolation, interleaved attribution, closed-at-startup re-admission DEMO+PAPER, identity never re-admitted,
  resting/partial counting, §6 risk scenarios, margin, panel). Full suite 1585 passed, 9 skipped.
- Live check (~21:15 GMT+4): runtime RUNNING DEMO, broker 0 positions / 0 orders, balance = equity 9,661.62,
  reconciliation CLEAN, 0 unresolved incidents. Live two-symbol verification PENDING: XAUUSD market closed
  (Saturday). Deployment of the fix requires a controlled flat restart with operator approval.

## Session: Multiple simultaneous trades — re-verification (2026-09-27, 01:25-01:50 GMT+4)

- Pre-work (read-only): DEMO runtime PIDs 25288/10512 and dashboard 14524/27832 still running from the main
  checkout at `7f604ab` (0.2.0), schema 29. Kill switch DISENGAGED, reconciliation CLEAN, heartbeat fresh,
  broker/local 0 open positions, 0 working orders, 0 unresolved incidents, balance = equity 9,622.52.
  Startup excluded XAUUSD (`stale_quote`, market closed); running symbols = BTCUSD only. Runtime untouched.
- Fresh 24 h evidence (to 21:29 UTC 2026-09-26): 36 BTCUSD fills, 0 XAUUSD/GBPJPY decisions (no market / not
  enabled), 0 correlation, aggregate-risk, portfolio or max-position blocks ever recorded in DEMO. Diagnosis
  unchanged: single-position behaviour is expected; ASN-016/017 fixes remain the only defects.
- Checked: ~40 `PROPOSED` orders (final-permission blocks, never sent) have no broker id and are excluded from
  slot counting, exposure and the same-symbol check -- not a blocker.
- Found (diagnostics only, not fixed): backlog #26, a stale `paper:global_block` event makes `why-no-trade`
  report "kill switch ENGAGED" while DISENGAGED.
- Tests added: interleaved exits across XAUUSD/BTCUSD keep each exit deal, POSITION_CLOSED event and Strategy
  Lab attribution on their own position/strategy; an unresolved UNKNOWN on XAUUSD blocks a BTCUSD entry
  (global policy, directive §30). Both passed first time (no defect). Readiness panel rendered against a
  scratch copy of the live DB. Full suite 1587 passed, 9 skipped.
