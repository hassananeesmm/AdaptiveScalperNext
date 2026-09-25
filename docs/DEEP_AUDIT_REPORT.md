# Deep Technical Audit Report

Audit date: 2026-09-25 (Windows 11, Python 3.13.15)  
Audit branch: `codex/deep-audit-20260925`  
Integration base: dashboard/Windows line through `60a4a15`; not merged to `main`.

## Executive result

The repository is suitable for continued live-data PAPER operation. It is **not ready for
new DEMO exposure**. The non-mutating preflight returns `READY_FOR_PAPER` and withholds
`READY_FOR_DEMO` because the operator-owned kill switch is `UNINITIALIZED`, no fresh CLEAN
DEMO reconciliation snapshot exists while PAPER is running, and GBPJPY slippage evidence is
insufficient. No `order_send` was performed during this audit.

The audit preserved the pre-existing work, created a verified SQLite online backup at
`data/backups/adaptive_scalper.pre-deep-audit.20260925T095839Z.sqlite3`, and used the branch
above. Source database and backup both returned `PRAGMA integrity_check=ok`.

## Repository and integration evidence

- The initial tree was clean on local `dashboard-review` at `d2ae27f`, two commits ahead of
  the then-remote dashboard branch. That line already contained `windows-validation`.
- `origin/main` remained at `8e67f77`; no merge to `main` occurred.
- A concurrent Windows-validation session advanced the shared checkout and remote dashboard
  branch through `60a4a15`. Those commits were preserved. Audit changes are layered on top.
- PR #2 could not be queried with GitHub CLI because `gh` is not installed; branch ancestry
  and fetched refs establish that the dashboard branch includes Windows-validation.

## Architecture and trust boundaries

The execution chain is:

`MT5 tick -> closed M5 bar -> causal features -> regime classifier -> six-strategy registry
-> selector -> cost/news/correlation/portfolio gates -> risk governor -> final permission
(twice) -> order_check (twice when required) -> sole new-entry order_send path -> fill and
position persistence -> position manager -> reconciliation -> SQLite -> read-only dashboard`.

Trust boundaries:

1. `gateway/mt5_gateway.py` translates untrusted broker structures into internal typed data.
2. `gateway/factory.py` is the live gateway construction site and returns a synchronized
   wrapper. Cross-process MT5 clients are not serialized by that in-process lock.
3. `core/final_permission.py` and `execution/service.py` are the final new-exposure boundary.
4. `risk/governor.py` is the sizing authority; hard ceilings are also validated in config.
5. SQLite WAL is authoritative local state. Migrations are transactional and the dashboard
   opens it with SQLite `mode=ro`.
6. ML, RAG and curated knowledge are advisory-only and have no permission, risk or send path.
7. The dashboard is a separate localhost GET/WebSocket observer and cannot mutate trading.

AST/architecture tests cover construction and send call sites. The only broker mutation
services are entry, close, stop modification and pending-order cancellation/recovery paths;
each rechecks DEMO identity/permissions appropriate to the mutation. PAPER does not call
`order_check` or `order_send`.

## Findings

### ASN-001 — Offline tests reached a live MT5 terminal

- Severity/type: **CRITICAL — confirmed defect, fixed before this audit**.
- Affected: `tests/conftest.py`, `tests/test_offline_mt5_guard.py`, live MT5 test opt-in.
- Actual/expected: an offline Windows suite could initialize the installed terminal; offline
  tests must never touch it.
- Reproduction/evidence: recorded in `WORKLOG.md` W1; the initial Windows run launched MT5.
- Root cause: a skip guard looked for a lazy-import module global that never existed.
- Consequence: unintended broker reads and potential progression to dry-run trade requests.
- Correction/regression: suite-wide import block except explicit `ASN_LIVE_MT5=1` tests.
- Verification/status: final full suite `1517 passed, 9 skipped`; **FIXED**.

### ASN-002 — Broker server timestamps were stored as UTC

- Severity/type: **HIGH — confirmed defect, fixed before this audit**.
- Affected: `gateway/server_time.py`, gateway conversions, migrations 27–28, history guards.
- Actual/expected: IC Markets server time was UTC+3 in US DST but rows were labelled UTC.
- Reproduction/evidence: live quote offset +10,800 seconds and FX close-hour analysis across
  212 weeks; first conversion found ten skipped-hour rows.
- Root cause: broker timestamps were assumed to be UTC.
- Consequence: news windows and causal time comparisons were shifted by hours.
- Correction: explicit `UTC+2/US_DST`, startup verification, quarantine of unconvertible
  rows, transactional conversion after backup.
- Regression/verification: server-time and time-basis tests plus live quote verification;
  current preflight reports UTC storage and verified clocks. **FIXED**, future DST transitions
  remain continuously checked.

### ASN-003 — Configuration could exceed documented risk ceilings

- Severity/type: **HIGH — confirmed defect, fixed before this audit**.
- Affected: `config/constants.py`, `config/loader.py`, `risk/governor.py`.
- Actual/expected: values up to 20% were accepted; no configuration may exceed
  0.25/0.75/2/5%, two positions total and one per symbol.
- Reproduction: construct `RiskConfig`/`RiskLimits` above a ceiling on the prior revision.
- Root cause: documentation defaults were mistaken for enforced maxima.
- Consequence: unsafe sizing and aggregate exposure.
- Correction/regression: `HARD_RISK_CEILINGS` enforced at both construction boundaries.
- Verification/status: risk tests and full suite pass. **FIXED**.

### ASN-004 — News HTTP latency could starve position review

- Severity/type: **HIGH — confirmed defect, fixed before this audit**.
- Affected: runtime scheduler/news monitor.
- Actual/expected: synchronous provider calls occupied the single MT5 scheduler thread;
  position review must remain higher priority and timely.
- Reproduction: deterministic hanging provider in `test_runtime_scheduling.py`.
- Root cause: network I/O ran in the scheduler callback.
- Consequence: delayed protective management.
- Correction: one background fetch at a time, two-second scheduler budget, one-second result
  polling; SQLite and broker calls remain serialized on the scheduler thread.
- Verification: live max scheduler lag about 1.04 seconds in the sampled PAPER runtime;
  deterministic starvation regression passes. **FIXED**.

### ASN-005 — Dashboard integrity check blocked every refresh

- Severity/type: **MEDIUM — confirmed defect, fixed before this audit**.
- Affected: `dashboard/health.py`, dashboard panel computation.
- Actual/expected: `PRAGMA integrity_check` took about six seconds per refresh; the observer
  should refresh independently without delaying itself or the runtime.
- Root cause: full integrity verification was on the request path.
- Correction: background read-only `IntegrityMonitor`, ten-minute TTL, honest PENDING state.
- Regression/verification: refresh-cost tests; current 19 KB `/api/panels` median 0.148 s,
  maximum 0.170 s over five requests. **FIXED**.

### ASN-006 — No comprehensive non-mutating preflight existed

- Severity/type: **MEDIUM — confirmed gap, fixed in this audit**.
- Affected: new `adaptive_scalper/preflight.py`, CLI registration, tests.
- Actual/expected: separate diagnostics existed, but no one command returned the required
  PAPER/DEMO readiness state and exact blockers.
- Reproduction: CLI parser had no `preflight` command.
- Root cause: earlier operational tooling was assembled incrementally.
- Consequence: an operator could confuse a healthy WebSocket or reachable MT5 terminal with
  complete DEMO readiness.
- Correction: read-only config/dependency/database/schema/time/account/symbol/clock/quote/
  news/reconciliation/UNKNOWN/risk-cost/kill-switch/dashboard checks. It never migrates,
  reconciles, clears controls, calls `order_check`, or calls `order_send`.
- Regression/verification: byte-for-byte no-write test and CLI tests pass; live result is
  `READY_FOR_PAPER`. **FIXED**.

### ASN-007 — Controlled DEMO gates are incomplete

- Severity/type: **HIGH — verified operational block, open**.
- Affected: operator state and current evidence, not a code defect.
- Actual/expected: kill switch is UNINITIALIZED; PAPER has no fresh reconciliation state;
  GBPJPY slippage is unknown. DEMO requires all three resolved.
- Reproduction/evidence: `python -m adaptive_scalper.cli preflight`.
- Consequence: no new DEMO exposure is authorized.
- Proposed correction: operator decides kill-switch state; start DEMO only afterward so it
  performs reconciliation; retain GBPJPY `BLOCK_COST` until sufficient evidence exists or
  exclude it from DEMO configuration. Never invent a value.
- Regression/verification/status: preflight must report `READY_FOR_DEMO`; **OPEN/BLOCKING**.

### ASN-008 — CLI status and health run a full integrity scan

- Severity/type: **LOW — confirmed performance finding, open**.
- Affected: `cli/system.py`, `dashboard/health.compute_health`.
- Actual/expected: `status` took 4.487 s and `health` 5.576 s on the 275 MB WAL database.
- Root cause: both synchronously run full `PRAGMA integrity_check`.
- Consequence: slow operator feedback only; trading is in another process and is unaffected.
- Proposed correction: keep `doctor`/preflight as authoritative full checks; consider a
  separately persisted, age-labelled integrity result for fast status output.
- Regression test: assert status uses recent evidence and labels its age, while doctor still
  performs a full check.
- Status: **OPEN, non-safety-critical**.

### ASN-009 — Model artifacts use pickle-compatible deserialization

- Severity/type: **MEDIUM — architectural risk, open**.
- Affected: `learning/training.py`.
- Actual/expected: SHA-256 is checked before `joblib.load`, which detects accidental changes
  but is not authenticity protection against an attacker able to replace both artifact and
  stored checksum.
- Consequence: arbitrary code execution if a malicious local artifact plus matching metadata
  is introduced.
- Proposed correction: keep artifacts local/non-downloadable, restrict directory ACLs, and
  migrate to a non-executable model format before accepting external artifacts.
- Regression test: checksum mismatch is already rejected; add provenance/ownership checks if
  external artifact ingestion is ever introduced.
- Status: **OPEN RISK; no external artifact path exists today**.

### ASN-010 — MT5 terminal selection is implicit

- Severity/type: **LOW — unverified broker/operations assumption**.
- Affected: live gateway initialization.
- Actual/expected: two terminals are installed and initialization does not pin a path.
- Consequence: attachment to an unintended terminal; DEMO gate still prevents REAL mutation.
- Proposed correction/test: optional configured terminal path plus identity assertion.
- Status: **OPEN**.

## Backtest, ML and knowledge conclusions

The backtest uses next-bar-open entry, executable bid/ask sides, conservative same-bar
SL/TP ordering, gap-through stop handling, commission/spread/slippage/swap accounting,
pending-entry persistence, risk halts and dataset/OOS ledgers. OOS safeguards prevent
overlap and reuse. These properties are heavily regression-tested, but historical results
remain limited by broker history coverage, assumed costs where evidence is absent, and the
absence of point-in-time news for ordinary historical runs.

ML remains Stage-1 observer-only. Training is temporal and checksum-validates locally
produced artifacts. RAG/OKF cannot authorize entries, change risk, promote models or clear
the kill switch. No paid LLM is required at runtime.

## Final disposition

- Confirmed defects fixed: ASN-001 through ASN-006 (some fixed in preceding Windows commits).
- Remaining confirmed defect: none known in a currently authorized PAPER send-free path.
- Remaining blocks/risks: ASN-007 through ASN-010.
- Live PAPER: genuine current MT5 data, fresh heartbeats, no task failures observed; zero
  trades is expected because the kill switch remains uninitialized.
- DEMO readiness: **NOT READY**.
- REAL/CONTEST/UNKNOWN execution: blocked by design; not tested by sending orders.
