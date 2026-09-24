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

5. [SEVERITY: MEDIUM, SUBSYSTEM: execution] `execution/service.py`'s
   `order_check_success_retcodes` default (`{0, 10009}`) has NOT been
   live-verified against the real DEMO terminal's actual `order_check()`
   return value — MT5 broker implementations are inconsistent about
   whether a "no error" `order_check()` returns `0` or the same
   `TRADE_RETCODE_DONE=10009` used for `order_send`. `order_check()` was
   deliberately NOT invoked live this checkpoint (CLAUDE.md: only
   read-only gateway calls may be exercised live; `order_check`, though
   non-executing, is a dry-run TRADE request and was treated
   conservatively as out of scope). Must be live-verified with a real
   (never-sent) `order_check()` call before controlled DEMO validation —
   tracked here so it isn't silently assumed correct.

7. [SEVERITY: LOW, SUBSYSTEM: execution] `execution/reconciliation
   .run_reconciliation()`'s `BrokerPositionSnapshot` construction passes
   the broker's raw `symbol` string (e.g. `"XAUUSDm"`) directly as
   `canonical_symbol`, without translating it through the broker-name
   resolver (`gateway/symbol_resolver.py`). Works today only because
   `reconcile_positions()` never actually compares `canonical_symbol`
   values against each other — it keys entirely on `broker_position_id`
   — but this is fragile if that function's contract ever changes. Needs
   a real translation step once reconciliation is wired into the runtime
   loop.

4. [SEVERITY: LOW, SUBSYSTEM: history] Tick history storage
   (`adaptive_scalper/history/store.py`) dedupes on
   `(canonical_symbol, time_msc)`. A future MT5 build/broker whose tick feed
   doesn't fill `time_msc` (or fills it with second-resolution granularity)
   could silently drop distinct ticks that collide on that key. Not
   observed so far;  `Mt5Gateway._tick_row` always reads the SDK's real
   `time_msc` field for range-fetched ticks, but flag for re-check if a
   live tick-bootstrap run ever reports suspiciously low counts vs. known
   volume.

9. [SEVERITY: LOW, SUBSYSTEM: execution] `execution/reconciliation
   .find_closing_deals()` treats an INOUT deal (MT5 `ENUM_DEAL_ENTRY=2`)
   purely as closing evidence for the reduced portion of a position's
   exposure. It does not attempt to open a NEW local position for
   whatever additional exposure the same INOUT deal may have opened in
   the other direction (a netting-account "flip" in one atomic broker
   operation) — no canonical-symbol/strategy context exists at
   reconciliation time to attribute a freshly-opened position to. Not
   observed in practice on this project's real IC Markets DEMO account
   (a hedging account, where OUT_BY is the relevant multi-position-close
   semantic instead); flag for a real design once/if a netting-mode
   account is ever connected.

10. [SEVERITY: MEDIUM, SUBSYSTEM: paper] `paper/engine.run_paper_cycle()`
   treats EVERY supplied bar as new on a session's first-ever cycle. A
   caller that seeds a fresh session with deep history gets that history
   replayed as trades labeled `PAPER_LIVE_DATA` (directive section 82
   evidence-class mixing). The runtime orchestrator (next checkpoint)
   must seed a new session with only a trailing `feature_lookback+1`-bar
   context window, or `run_paper_cycle()` must gain an explicit
   "start from now" cursor. Not reachable today: nothing calls it yet.

11. [SEVERITY: LOW, SUBSYSTEM: backtest] `backtest/oos.run_untouched_oos()`
   checks the usage ledger, runs, THEN records its own usage, in
   separate transactions. Two concurrent OOS runs over overlapping
   ranges could both pass the check. Single-process research use only
   today; wrap check+record in one `BEGIN IMMEDIATE` if OOS runs ever
   become concurrent.

12. [SEVERITY: LOW, SUBSYSTEM: tooling/tests] `tests/test_guardrails.py::
   test_outside_project_root` asserts Windows path semantics
   (`C:\Windows\...` is outside `C:\AdaptiveScalperNext`) and fails when
   pytest runs on Linux (the cloud runner), because `os.path` there
   doesn't parse drive letters. The guardrail hook only ever runs on the
   Windows laptop, where the test passes. Not a trading-logic defect;
   fix is to use `ntpath` explicitly in `.claude/hooks/guardrails.py` or
   mark the test Windows-only -- left for the owner's decision since the
   hook is the project's own PreToolUse safety gate.

13. [SEVERITY: LOW, SUBSYSTEM: backtest] `holding_seconds` in the
   backtest/PAPER review is `bar.time - entry_time_utc` (bar OPEN times),
   so a review at a bar's close under-counts the real holding time by
   one bar. With `max_holding_seconds=600` on M5 that is a one-bar-late
   max-holding exit. Needs the resolution's bar duration to fix
   honestly; deliberately not changed in the 2026-09-24 correctness
   checkpoint to keep that change reviewable.

## Fixed

- ~~[SEVERITY: HIGH, SUBSYSTEM: backtest/paper] Six PAPER/backtest
  correctness defects in `backtest/engine.py`, `paper/`, and
  `backtest/oos.py` (2026-09-24 review): (1) a selected entry on the
  last bar of an incremental call was a local variable and was silently
  DROPPED at every PAPER cycle boundary; (2) an adaptive FULL_CLOSE
  decided at bar i's close filled at bar i's own OPEN (a price from
  before the information existed) and the range-end close filled at
  the last bar's open; (3) the entry bar was skipped (`continue`), so a
  stop or target hit inside the entry bar was never seen and the regime
  tracker missed that bar; stops triggered on mid instead of bid/ask
  and a gapped-through stop filled at the stop price; (4) `peak_r` was
  passed as `current_r` every bar, so profit-giveback protection could
  never fire in simulation (live persists a monotonic peak); (5) entry
  spread+slippage was deducted a second time from P/L already computed
  from the entry execution price, while commission, swap and exit
  friction were never charged or reported; (6) OOS contamination was
  checked on the exact checksum only, so any shifted/nested/other-
  resolution overlap passed. Also: a PAPER cycle with exactly one new
  bar was a no-op (live PAPER lagged one bar), and too-short bar
  history could make a cycle skip new bars silently.~~ Fixed 2026-09-24.
  New: `backtest.types.PendingEntryState`, `OpenPositionState.peak_r`/
  `.pending_exit_reason`, `BacktestResult.pending_entry`,
  `run_backtest(resume_pending_entry=...)`, `simulate_fill(at=...)`,
  `dataset.find_overlapping_usage()`, migration
  `0019_paper_pending_entry`. Regression suite:
  `tests/test_backtest_correctness_regressions.py` (41 tests, including
  a real-strategy incremental-vs-continuous property test over 12
  seed/chunk combinations). Two existing tests were corrected, not
  weakened: `test_backtest_engine.py`'s regime-resume test moved its
  boundary from bar 200 (where an unresumed tracker happens not to
  diverge on that clean-trend fixture) to 203; with-resume was verified
  to match at every boundary 60-214. `test_backtest_oos.py`'s
  "unrelated dataset" test used the same calendar period with different
  prices -- which IS contamination -- so it now uses a genuinely
  disjoint period, and the same-period case is asserted to block.

- ~~[SEVERITY: HIGH, SUBSYSTEM: backtest/paper] While building PAPER mode
  (directive section 132), `run_backtest()` was extended with an
  incremental resume mode (`resume_open_position`/
  `force_close_at_range_end=False`) so an ongoing PAPER cycle could
  continue managing a still-open simulated position across calls
  instead of force-closing it every cycle. A strict "does resuming in
  chunks match one continuous run" test caught a real divergence:
  `regimes.classifier.RegimeTracker`'s hysteresis state (confirmed/
  candidate/candidate_count) was rebuilt from `UNKNOWN` on every
  `run_backtest()` call, with no way to resume it — an incremental
  caller restarting the tracker every cycle genuinely diverges from
  what a continuously-running tracker would have decided, defeating
  directive section 13's "do not flip on one noisy bar" guarantee
  across process/cycle boundaries.~~ Caught and fixed before ever being
  committed, 2026-09-21: `RegimeTracker` gained `initial_candidate`/
  `initial_candidate_count` constructor params and a `.state` property;
  `run_backtest()` gained `resume_regime_tracker: RegimeTrackerState |
  None` and always returns `BacktestResult.final_regime_tracker_state`.
  Regression: `test_run_backtest_resume_regime_tracker_reproduces_
  continuous_processing` explicitly shows the divergence WITHOUT
  resuming it and exact match WITH it (trade-for-trade, including the
  still-open position); `tests/test_paper_engine.py::test_run_paper_
  cycle_incremental_feeding_matches_a_single_shot_backtest` proves the
  same property end-to-end through `paper/engine.py`.

- ~~[SEVERITY: CRITICAL/HIGH/MEDIUM, SUBSYSTEM: execution/position_management/
  gateway] External review of the working tree ahead of commit 2bd1bf0
  (post-17-findings), 16 new findings before resuming backtest/ML/PAPER
  work: (1) `_verify_critical_broker_state()` computed `now` ONCE and
  reused it for BOTH quote-freshness checks in a two-round verify, so a
  stale tick could pass round 2 even though wall-clock time had genuinely
  advanced past the freshness threshold; (2) the execution-owned symbol
  check only verified `trade_mode != DISABLED`, never independently
  re-verifying directional trade-mode permission (LONGONLY/SHORTONLY/
  CLOSEONLY) or asset identity against FRESH symbol metadata — it
  trusted the caller's `FinalPermissionInput` for both; (3)
  `execution/close.py` remained materially older than
  `stop_modification.py` (symbol_info fetched twice, filling policy from
  round 1 only, no second `order_check` before send); (4) stop/close
  result-state interpretation collapsed CANCELLED/RESTING/PARTIAL into a
  single `SENT`, losing the distinction between "broker proved this
  happened" and "broker accepted but hasn't confirmed yet"; (5-6)
  partial-fill residual risk (requested/filled/remaining volume and
  monetary risk) was not persisted as durable typed fields, so a RESTING
  order's pending risk couldn't be reconstructed from SQLite + broker
  truth after a restart; (7-10) `create_local_position()` silently kept
  stale first-fill data on a SECOND partial fill into the same
  `broker_position_id`; `entry_price` could be persisted as
  `result.price_filled or 0.0` (a real zero on ambiguous fills); only a
  `positions` row was written, never the individual entry deals; the
  local `deals` schema dropped broker `fee`; (11) UNKNOWN-without-
  broker-id resolution matched on token+symbol alone, not
  direction/volume/magic/time-window, risking a false match to an
  unrelated order sharing a token prefix; (12) `run_reconciliation()`
  only reconciled `positions_get()`, never `orders_get()` — a broker
  pending order with no local counterpart (or vice versa) went entirely
  unnoticed; (13) `_maybe_record_exit_fill()` queried only the LATEST
  closing deal, undercounting P/L on multi-deal/partial closes; (14)
  `record_exit_fill()`'s `realized_slippage` parameter existed but the
  manager never computed/supplied it; (15) `record_incident()` created
  an unbounded new row every ~0.5-1s reconciliation cycle for the SAME
  unresolved mismatch; (16) `PROJECT_STATUS.md` still had a stale
  Gateway Protocol paragraph claiming `order_send`/`order_check`/
  `positions_get`/`orders_get` were deliberately excluded, a stale
  "Schema version 13" section, and a stale "no order_send/order_check
  exists anywhere" paragraph, all left over from before this and the
  prior checkpoint's execution work landed.~~ Fixed 2026-09-21. New:
  `adaptive_scalper/execution/entry_fills.py`
  (`record_entry_fills()`/`EntryFillRecordResult`, refuses to mutate a
  position once R-management is active), migrations `0015_entry_fills`
  (durable `requested_monetary_risk`/`filled_volume`/
  `filled_initial_monetary_risk`/`remaining_volume`/
  `remaining_pending_monetary_risk` on `orders`; `fee`/`entry_type`/
  `deal_type`/`broker_order_ticket`/`magic`/`comment` on `deals`),
  `0016_order_magic`, `0017_incident_dedup` (`dedup_key`/
  `first_seen_at_utc`/`last_seen_at_utc`/`occurrence_count` on
  `execution_incidents`). Rewrote: `execution/service.py`
  (`_verify_critical_broker_state()` takes an injectable
  `clock: Callable[[], float]`, calls it independently each round; fetches
  `symbol_spec` once per round and checks `identity_matches_canonical()`
  + `validate_direction_for_new_exposure()` against it, never trusting
  the caller's evidence alone), `execution/stop_modification.py` (same
  clock-injection pattern; added `CANCELLED` as a distinct outcome),
  `execution/close.py` (full rewrite to the stop_modification.py
  standard: `FULLY_CLOSED`/`PARTIAL_CLOSE`/`RESTING`/`CANCELLED`/
  `REJECTED`/`UNKNOWN` as distinct outcomes, a partial close updates
  local volume/risk from broker truth via
  `_apply_partial_close_to_local_state()`), `execution/unknown.py`
  (direction/volume/magic/time-window compatibility required in all 4
  evidence-source loops, not just token+symbol), `execution/
  reconciliation.py` (`reconcile_pending_orders()`/
  `_recover_missing_local_order()` reconcile `orders_get()` against
  local active orders; `record_incident()` now takes a `dedup_key` and
  updates an existing unresolved row's `last_seen_at_utc`/
  `occurrence_count` instead of inserting a duplicate),
  `position_management/manager.py`
  (`_maybe_record_exit_fill()` aggregates every closing deal, not just
  the latest, and computes+persists `realized_slippage`),
  `execution/position_resolution.py`
  (`resolve_entry_fill_evidence()` — weighted-average price across every
  matching entry deal, never a bare `result.price_filled or 0.0`).
  `PROJECT_STATUS.md`'s Gateway Protocol paragraph, "Schema version"
  section (now 17), and "no order_send exists" / "BLOCK_SYMBOL_NOT_ALLOWED
  does not exist" paragraphs all corrected to current reality. 1024 tests
  passing (up from 961).

- ~~[SEVERITY: HIGH, SUBSYSTEM: execution/position_management/portfolio]
  External review of commit cbe16b3, 17 findings before PAPER/controlled-
  DEMO validation: (1-3) `execution/stop_modification.py` reverified DEMO
  only before `order_check`, not again before `order_send`, reused round
  1's take-profit/symbol/tick state for the actual send, and never
  rechecked stops/freeze/quote freshness immediately before send; (4)
  `position_management/manager.py` recorded `request_at_utc` even for
  pre-send blocks that never reached the broker; (5)
  `state_store.get_or_create_state()` silently kept the old row on a
  conflicting `initial_monetary_risk`/`entry_regime`, and didn't validate
  positive/finite; (6) an invalid initial risk was indistinguishable from
  a healthy HOLD; (7) no `POSITION_REVIEWED`/`STOP_ADVANCED` journal
  events existed; (8) `record_exit_fill()` was never called (this item);
  (9) a resolvable/unresolvable `PARTIAL` fill never became accounted
  local exposure, and — a pre-existing gap this review surfaced — NOTHING
  in this codebase had ever created a `positions` table row for a real
  entry at all; (10-11) `portfolio/exposure.py` only folded pending
  positions into the total-risk sum, leaving per-symbol/currency/USD/
  cluster heat blind to resting orders, and `open_symbols` was misleadingly
  named; (12) `execution/service.py` trusted DEMO/kill-switch/symbol state
  solely from caller-supplied `FreshEvidence`; (13) no authoritative
  final margin/broker recheck immediately before send; (14)
  `execution/request_token.py` used a `client_request_id[:16]` prefix
  slice, collision-prone for structured ids; (15)
  `execution/reconciliation.py` only recorded the LATEST `OUT` deal,
  ignoring `INOUT`/`OUT_BY` and multi-deal partial closes, and falsely
  linked a closing deal's `order_id` to the entry order; (16) the local
  recovery write (position + deals + journal) was not atomic; (17)
  `PROJECT_STATUS.md` had several statements stale relative to the actual
  codebase (Phase 5 claiming no continuous-expectancy/manager loop, a
  hardcoded schema version, "order_send does not exist" prose left over
  from before Phase 4).~~ Fixed 2026-09-21. New:
  `position_risk_incidents` (migration `0013_position_risk_quarantine`),
  `state_store.PositionStateConflictError`/`record_risk_incident()`/
  `has_unresolved_risk_incident()`, `close.POST_SEND_STATUSES`,
  `execution/store.create_local_position()`,
  `journal.events._append_event_locked()`,
  `reconciliation.find_closing_deals()`. Rewrote:
  `execution/stop_modification.py` (both rounds now identical --
  independent DEMO/symbol/tick/stops/freeze re-verification, request
  rebuilt from fresh state each round, a changed request re-`order_check`ed
  before send), `position_management/manager.py` (quarantine path,
  post-send-only request timestamps, `POSITION_REVIEWED`/`STOP_ADVANCED`
  journaling, real `record_exit_fill()` wiring), `execution/service.py`
  (`_verify_critical_broker_state()` called twice, independently, for
  DEMO/kill-switch/symbol/quote truth; a second `order_check` + fresh
  margin recheck immediately before send; FILLED/PARTIAL paths now create
  a real local position), `portfolio/exposure.py` (`compute_exposure()`
  folds pending positions into every heat dimension, not just the total),
  `execution/reconciliation.py` (`_recover_missing_local_position()` now
  takes every closing deal, aggregates them, resolves `order_id` per-deal
  from real local `orders` rows or NULL, and journals inside the SAME
  transaction as the position/deal writes). Renamed `open_symbols` ->
  `open_or_pending_symbols` across `core/final_permission.py`/
  `portfolio/correlation.py`. 961 tests passing (up from 916).

- ~~[SEVERITY: HIGH, SUBSYSTEM: execution/core/position_management]
  Round-2 external review, the remaining 5 HIGH findings: (4)
  `run_reconciliation()`'s `RECOVERED` status didn't actually repair
  anything; (5) `PROJECT_STATUS.md`'s "Current phase" header was stale
  (fixed as part of the CRITICAL-findings checkpoint above); (6)
  `BLOCK_PORTFOLIO_RISK` was an unintegrated gap; (7) UNKNOWN resolution
  had no path at all for a send that lost broker acknowledgement
  entirely; (8) no continuous position-expectancy engine existed.~~
  Fixed 2026-09-20. (4): `run_reconciliation()` now queries
  `history_deals_get()` for the exact position's authoritative closing
  deal and atomically repairs local state (marks CLOSED, records the
  real deal, journals `POSITION_CLOSED`) — `RECOVERED` only appears once
  that repair has genuinely happened; an unrepairable
  `MISSING_LOCAL_POSITION` now correctly stays `BLOCKING_MISMATCH` with
  an incident recorded, never silently relabeled. (6): new
  `portfolio.exposure.evaluate_portfolio_risk_gate()` — per-symbol,
  net-currency-direction, and correlated-cluster heat ceilings, all
  bounded by the same `max_total_open_risk_pct`
  `risk.governor.evaluate_risk_gate()` already enforces, wired into
  `core/final_permission.py` as `BLOCK_PORTFOLIO_RISK`. (7): new
  `execution/request_token.py` (a compact deterministic token
  `execution/service.py` now embeds in every `OrderRequest.comment`) and
  `execution.unknown.resolve_unknown_order_without_broker_id()` — a
  secondary correlation path keyed on that token (narrowed by broker
  symbol), resolving only when every match agrees, remaining UNKNOWN on
  any ambiguity. (8): new `position_management/expectancy.py`
  (`evaluate_position_expectancy()`) — the "if I were flat now, would I
  still open this?" decision core, feeding `thesis_valid`/
  `regime_reversed` into `adaptive_exit`; RAG/ML evidence is recorded but
  deliberately cannot by itself flip the verdict, consistent with both
  subsystems' own advisory-only/observer-only contracts. 880 tests
  passing (up from 855).

- ~~[SEVERITY: CRITICAL, SUBSYSTEM: execution] Round-2 external review, 3
  CRITICAL findings: (1) fresh pre-send safety was documented but not
  structurally enforced -- a caller could pass a stale `FinalPermissionInput`;
  (2) `order_send` retcodes were reduced to a "10009 or REJECTED" binary,
  misclassifying `DONE_PARTIAL`/`PLACED` (real broker state) as rejections;
  (3) `execution/close.py` sent directly via `gateway.order_send()` without
  the same pre-send protections a new entry gets.~~ Fixed 2026-09-20 — see
  PROJECT_STATUS.md's "Execution-safety review round 2" section for full
  detail. New: `gateway/retcodes.py`. Rewrote:
  `execution/service.py` (evidence-builder-callable pattern, called
  twice), `execution/close.py` (same two-round-of-checks pattern, DEMO-
  verification-scoped rather than full final-permission-gated). Widened:
  `execution/state_machine.py`'s `SUBMITTED` transitions. 5 more HIGH
  findings from the same review remain open — see items 0a-0d above.

- ~~[SEVERITY: CRITICAL, SUBSYSTEM: execution] 9 execution-safety issues
  found by external review of the Phase 4 execution layer, before
  controlled-DEMO execution could be considered: (1) order ticket treated
  as position ticket; (2) no dedicated safe-close path (risked reverse
  exposure); (3) `execution/unknown.py` stale, resolved only against
  order history; (4) `execution/reconciliation.py` stale, no real
  gateway-truth orchestration; (5) `core/final_permission.py` missing
  real `BLOCK_RECONCILIATION`/`BLOCK_UNKNOWN_ORDER`/`BLOCK_DUPLICATE`/
  `BLOCK_REENTRY_CHURN` evidence; (6) idempotency collision on a
  DIFFERENT request silently returned the stale row; (7) filling type
  hardcoded to `ORDER_FILLING_IOC` regardless of broker support; (8)
  `order_check` not enforced as mandatory before `order_send`; (9) no
  guarantee of a fresh pre-send recheck.~~ Fixed 2026-09-20 — see
  PROJECT_STATUS.md's "Execution-safety review fixes" section for full
  detail per item. New: `execution/position_resolution.py`,
  `execution/close.py`, `execution/service.py`,
  `gateway/broker_constraints.py`,
  `tests/test_architecture_execution_boundary.py`. Rewrote:
  `execution/unknown.py`, `execution/reconciliation.py`. Hardened:
  `execution/store.py` (`IdempotencyConflictError`),
  `core/final_permission.py` (4 new integrated gates),
  `gateway/mt5_gateway.py`/`gateway/fake_gateway.py` (position-id
  never invented; filling type resolved, not hardcoded).
  Live-verified (read-only): real DEMO terminal filling_mode/
  positions_get/orders_get — see PROJECT_STATUS.md.

- ~~[SEVERITY: HIGH, SUBSYSTEM: risk] `evaluate_risk_gate()` trusted that
  `proposed_monetary_risk` had been correctly derived from
  `calculate_safe_volume()`, with no independent verification of the
  per-trade risk ceiling.~~ Fixed 2026-09-20 (external review, caught
  before this had ever been composed into the final permission gate):
  `evaluate_risk_gate()` now independently checks
  `proposed_monetary_risk` is positive/finite and
  `<= equity * risk_per_trade_pct / 100` as its FIRST check, regardless
  of how the proposal was computed upstream. Regression test:
  `test_risk_gate_blocks_a_tampered_oversized_proposal_even_with_room_elsewhere`.

- ~~[SEVERITY: MEDIUM, SUBSYSTEM: risk] The total-open-risk ceiling
  counted only `current_total_open_risk`, ignoring pending (resting,
  not-yet-filled) orders — pending risk could accumulate without
  counting toward the limit.~~ Fixed 2026-09-20: `RiskGateInput` gained
  `current_total_pending_risk`; the ceiling is now
  `open + pending + proposed <= max_total_open_risk_pct`. Regression
  tests: `test_risk_gate_blocks_on_pending_risk_alone`,
  `test_risk_gate_blocks_when_open_plus_pending_plus_proposal_crosses_limit`.

- ~~[SEVERITY: HIGH, SUBSYSTEM: costs] `estimate_cost()` defaulted
  commission/slippage/swap to `0.0` — a caller that simply forgot to
  measure one of them got "this cost is exactly zero" instead of an
  error, risking a silently underestimated cost bar.~~ Fixed 2026-09-20:
  all four cost components are now required keyword arguments (omitting
  one raises `TypeError` immediately). New `estimate_cost_from_evidence()`
  is the required real-runtime entry point — `float | None` per
  component, returns `None` (→ `BLOCK_COST`) if any is unknown, rather
  than ever calling `estimate_cost()` with a guessed zero. Regression
  tests: `tests/test_cost_model.py`'s `test_evidence_*` cases.

- ~~[SEVERITY: MEDIUM, SUBSYSTEM: portfolio] `evaluate_correlation_gate()`
  treated an N/A correlation against an open/pending position as "no
  conflict" — unresolved correlation is not evidence of safety, but the
  gate behaved as if it were.~~ Fixed 2026-09-20:
  `evaluate_correlation_gate()` gained `treat_missing_as_blocking`
  (default `True`): N/A against a real open/pending position now blocks
  (`BLOCK_CORRELATION`) conservatively.
  `compute_pairwise_correlation()`'s own reporting is unchanged (still
  honestly `None`, never `0.0`) — this fix is entirely about what the
  gate does with that honest N/A. Regression test:
  `test_gate_blocks_conservatively_on_missing_correlation_data_by_default`,
  exercised end-to-end through the composed gate in
  `test_blocks_on_na_correlation_with_open_position_by_default`.

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
