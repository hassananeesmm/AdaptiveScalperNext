# BUG BACKLOG

Non-blocking known issues. Append as discovered; strike through (don't
delete) once fixed, with the fixing commit/date noted.

## Open

1. **security-guidance plugin's agent-sdk-venv is a machine-global,
   non-git-tracked resource.** `~/.claude/security/agent-sdk-venv`
   (Python 3.14) can independently go stale/broken (observed once this
   session: `claude_agent_sdk` transiently not importable, stale
   `.building` lock present) without any change to this repository.
   `scripts/setup_claude_hooks.ps1` checks it on every run and warns if
   broken, but does not auto-repair it (repair = re-running pip install,
   which the SessionStart hook does automatically in the background on a
   fresh session anyway). Not blocking because `.claude/hooks/guardrails.py`
   (this project's own PreToolUse gate) does not depend on it at all.

2a. [SEVERITY: LOW, SUBSYSTEM: dashboard] `starlette.testclient` (via
   FastAPI's `TestClient`) emits a `StarletteDeprecationWarning` about
   `httpx` vs `httpx2` on every test run. Third-party warning, not our
   code; not actionable without a starlette/fastapi upgrade or switching
   test client libraries. Non-blocking; revisit at next dependency bump.

2b. ~~[SEVERITY: LOW, SUBSYSTEM: gateway/dashboard] `Mt5Gateway`'s
   underlying `MetaTrader5` module calls are synchronous and not
   documented by the vendor as safe for concurrent multi-thread use.~~
   Fixed: `adaptive_scalper/gateway/synchronized_gateway.py`'s
   `SynchronizedGateway` wraps any `Gateway` and serializes every call
   through one `threading.RLock`. `cmd_dashboard` (cli.py) now wraps the
   real `Mt5Gateway` in it before injecting into `create_app()`;
   `dashboard/app.py`'s docstring documents this as a hard requirement
   for any real gateway. Regression test:
   `tests/test_synchronized_gateway.py::test_serializes_concurrent_calls_across_threads`
   (with a companion unsynchronized-baseline test proving the probe can
   actually detect a missing lock, not just trivially pass). Not yet
   wired into anything beyond the dashboard, since the entry
   scanner/position manager/history jobs that would also need it don't
   exist yet — revisit as each of those lands to make sure they share one
   `SynchronizedGateway` instance rather than each constructing their own
   `Mt5Gateway`.

3. [SEVERITY: LOW, SUBSYSTEM: history] `adaptive_scalper/history/jobs.py`'s
   `get_or_create_job()` supports extending an existing job's
   `requested_end_utc` forward (incremental resync) but raises `ValueError`
   if ever asked to widen `requested_start_utc` backward (pull MORE history
   than a prior job already committed to) — the single-cursor checkpoint
   model can't safely reinterpret "resume" in that direction. Not expected
   in normal operation (the 5-year bar / 30-day tick windows are fixed
   defaults), but if a future need arises to backfill deeper history for an
   already-bootstrapped symbol, this needs a real design (e.g. a second job
   walking backward from the original start), not a workaround.

4. [SEVERITY: LOW, SUBSYSTEM: history] Tick history storage
   (`adaptive_scalper/history/store.py`) dedupes on
   `(canonical_symbol, time_msc)`. A future MT5 build/broker whose tick feed
   doesn't fill `time_msc` (or fills it with second-resolution granularity)
   could silently drop distinct ticks that collide on that key. Not
   observed so far;  `Mt5Gateway._tick_row` always reads the SDK's real
   `time_msc` field for range-fetched ticks, but flag for re-check if a
   live tick-bootstrap run ever reports suspiciously low counts vs. known
   volume.

## Fixed

- ~~[SEVERITY: HIGH, SUBSYSTEM: gateway/symbol_validation] First draft of
  the external-review "canonical asset identity" fix required an EXACT
  `currency_base` match (e.g. BTCUSD must report `currency_base="BTC"`)
  for all three canonical symbols.~~ Live-verified against this project's
  real IC Markets DEMO terminal before commit: BTCUSD actually reports
  `currency_base=currency_profit=currency_margin="USD"` — this broker (and
  plausibly others) settles/margins crypto CFDs entirely in USD and
  doesn't use `currency_base` to name the crypto asset at all. The naive
  exact-match design would have permanently failed closed on the genuine,
  correctly-name-resolved BTCUSD instrument on this broker — defeating the
  exact three-symbol universe rather than protecting it. Fixed before ever
  reaching a committed state: `EXPECTED_IDENTITY` in
  `gateway/symbol_validation.py` now uses a per-symbol spec —
  `base_currency` is checked only for true FX/metal pairs (XAUUSD,
  GBPJPY), where it's reliable; BTCUSD's identity instead rests on
  `profit_currency="USD"` plus the broker's own `description` containing
  "bitcoin"/"btc", two independent signals. Regression tests:
  `tests/test_symbol_validation.py::test_btcusd_identity_does_not_require_btc_as_currency_base`
  and `::test_btcusd_still_fails_closed_on_wrong_profit_currency_or_description`.
  Re-verified live after the fix: all three canonical symbols now
  correctly validate as `VALID` against the real broker.

- ~~`migrate()` wrapped `executescript()` in a manual `BEGIN`/`COMMIT`,
  but `executescript()` issues its own implicit `COMMIT` first, breaking
  the transaction (`sqlite3.OperationalError: cannot commit - no
  transaction is active`).~~ Fixed 2026-09-19, commit 4bd2f1b — execute
  each statement individually inside one real transaction instead.

- ~~`_split_statements()`'s naive `;`-split also mis-split on a `;`
  INSIDE a `--` SQL comment (migration 0002's comment text), leaving
  bare text as SQL and raising a syntax error.~~ Fixed 2026-09-19,
  commit 93b16b1 — strip `--` line comments before splitting. (Superseded
  by the full tokenizer replacement below, which removed the
  comment-stripping regex entirely in favor of a general fix.)

- ~~`_split_statements()` was a naive `;`-split, not a real SQL
  tokenizer — flagged as a known limitation that would mis-split a
  migration containing a trigger body or string literal with an
  embedded `;`, and the journal migration (0006, immutability enforced
  via `CREATE TRIGGER ... BEGIN ... END`) hit exactly that.~~ Fixed
  2026-09-20: replaced with a `sqlite3.complete_statement()`-based
  scanner in `adaptive_scalper/persistence/database.py`. Algorithm: scan
  character by character; each `;` triggers a
  `sqlite3.complete_statement(buffer)` check against the accumulated
  buffer — SQLite's own C library boundary oracle (`sqlite3_complete()`),
  which is comment- and string-literal-aware and correctly tracks
  `CREATE TRIGGER ... BEGIN ... END` nesting. Only when the buffer is a
  genuinely complete statement is it emitted and the buffer reset; a `;`
  inside a comment, a quoted string, or a trigger body just keeps
  accumulating. This let the previous `_LINE_COMMENT_RE` comment-strip
  step be removed entirely — `complete_statement()` already handles
  comments correctly, and stripping them separately was itself the
  source of the very first fix above. A migration ending in genuinely
  incomplete SQL now raises `MigrationError` (not silently dropped),
  while a trailing comment-only remainder is correctly treated as
  nothing to execute.
  Regression tests: `tests/test_migration_parser.py` (12 tests) —
  ordinary/multiple statement splitting, two statements on one physical
  line, a semicolon inside a quoted string literal, a semicolon inside a
  comment, a trigger body with two internal `RAISE` statements emitted
  as exactly one statement, an end-to-end migration mixing
  `CREATE TABLE`/`CREATE TRIGGER`/`CREATE TABLE` that both applies AND
  whose trigger genuinely blocks an `UPDATE`, transactional rollback of
  earlier statements when a later one fails, incomplete trailing SQL
  raising, and idempotency. Also live-verified end-to-end: migration
  0006 applies against a fresh database alongside migrations 1-5, and
  the resulting `journal_events` immutability triggers genuinely block a
  real `UPDATE`/`DELETE` attempt (`sqlite3.IntegrityError`, not merely
  "no test caught a problem").

- ~~[SEVERITY: HIGH, SUBSYSTEM: gateway/symbol_resolver] Symbol alias
  regex alias-matched `XAUUSDT` (gold priced in Tether — a genuinely
  different instrument on many brokers/exchanges) to canonical
  `XAUUSD`.~~ Symptom: `resolve_symbol("XAUUSD", [SymbolSpec(name=
  "XAUUSDT", ...)])` returned `resolved=True, reason=ALIAS_MATCH`
  instead of failing closed. Suspected cause: the alias regex's
  no-delimiter suffix branch (`[^A-Za-z0-9]{0,1}[A-Za-z0-9]{0,3}`)
  allowed any case/any alnum suffix, so an uppercase currency-code-like
  suffix was indistinguishable from a lowercase broker marker like
  `XAUUSDm`. Found by external security review of an early commit;
  confirmed real (not a false positive) by direct regex reproduction
  before fixing. Fixed 2026-09-19 — scoped case-insensitivity to just
  the canonical-symbol portion via `(?i:...)`, and require a
  no-delimiter suffix to be lowercase-only. Regression test:
  `tests/test_symbol_resolver.py::test_currency_like_suffix_does_not_alias_match`.

- ~~[SEVERITY: HIGH, SUBSYSTEM: core/kill_switch] A missing `app_state`
  row for the kill switch was treated as `engaged=False` (disengaged) —
  meaning a fresh/never-initialized database silently permitted new
  exposure.~~ Symptom: `get_state()` on a freshly migrated DB with no
  prior `engage()`/`clear()` call returned `engaged=False`. Suspected
  cause: `_DEFAULT` sentinel used `engaged=False` rather than an explicit
  "we don't know" status. Found by external architecture review. Fixed
  2026-09-19 — replaced the boolean `engaged` field with a
  `KillSwitchStatus` enum (`UNINITIALIZED`/`INVALID`/`ENGAGED`/
  `DISENGAGED`); `blocks_new_entries` is True for every status except
  explicit `DISENGAGED`. Also fixed in the same pass: state+audit writes
  are now one atomic transaction (were two independent autocommit
  statements), and `clear()`/`bootstrap()` now require a typed
  `OperatorAuthority` object instead of an unforgeable-in-name-only
  `actor_role: str` argument. Regression tests:
  `tests/test_kill_switch.py` (`test_fresh_db_is_uninitialized_and_blocks_new_entries`,
  `test_corrupted_app_state_row_is_invalid_and_blocks_new_entries`,
  `test_state_write_and_audit_row_are_never_observed_out_of_sync`,
  `test_clear_rejects_every_non_operatorauthority_value`).

- ~~[SEVERITY: HIGH, SUBSYSTEM: dashboard] `dashboard/app.py`'s
  `/api/health` endpoint crashed with `sqlite3.ProgrammingError: SQLite
  objects created in a thread can only be used in that same thread`.~~
  Suspected cause: `create_app()` took a live `sqlite3.Connection` and
  captured it in the endpoint closure, but FastAPI dispatches sync
  endpoints onto a worker thread pool (`anyio.to_thread.run_sync`) —
  the connection, created on the main/fixture thread, was then used from
  a different thread. Found immediately by the endpoint's own test
  (`tests/test_dashboard_health.py`), before any commit. Fixed
  2026-09-19 — `create_app()` now takes a DB **path**, not a connection,
  and opens a short-lived connection per request
  (`contextlib.closing(connect(db_path))`); WAL mode makes this cheap
  and supports concurrent readers.
