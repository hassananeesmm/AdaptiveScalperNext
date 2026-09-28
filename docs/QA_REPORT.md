# QA report

## Integrated DEMO safety verification (2026-09-28) -- current

Branch `fix/integrated-demo-safety-20260928`, canonical Windows venv (`C:\AdaptiveScalperNext\.venv`, Python 3.13).
Evidence levels are kept separate: (1) source reviewed, (2) deterministic/fake tests, (3) Windows local,
(4) live MT5 DEMO.

- compileall: OK.
- Focused critical set (the 21 suites named in the audit brief + 3 new): **502 passed / 0 failed** at `0f3f4ab`.
- Full suite: **1716 passed / 9 skipped / 0 failed** (compileall OK; skips: 8 opt-in live-MT5 tests -- run separately with `ASN_LIVE_MT5=1`: **8 passed**, read-only -- and 1 symlink test this Windows user lacks the privilege for) at `b9cc0cb`.
- New regression tests were run against the pre-fix code and fail there (19/20 pending-entry, 2/3
  reconciliation-freshness; the passing ones are positive controls).
- Live MT5, read-only (level 3, not level 4 -- no order was sent): pinned IC Markets terminal initialises;
  the other install is refused; account trade_mode DEMO; terminal/account trading permitted; positions_get,
  orders_get, history_deals_get, history_orders_get work; XAUUSD/BTCUSD/GBPJPY quotes fresh under
  `UTC+2/US_DST` (age ~ -0.9 s, i.e. within tolerance); an unknown-symbol `copy_rates_from_pos` raises
  `Mt5QueryError` (MT5 `(-1, 'Terminal: Call failed')`) and a genuinely empty history range returns `[]`.
- DB: online backup of the production DB, quick_check/integrity ok, 0 FK violations, schema 29, equal counts; no
  migration pending.
- Security: tracked-file secret scan clean; pip check clean; pip-audit no known vulnerabilities; pyflakes no
  errors; bandit -ll 10 medium / 0 high, all pre-existing.

## Windows laptop QA (2026-09-25) -- historical

- **Machine:** Windows 11 Home 10.0.26200, `.venv` Python 3.13.15, SQLite 3.50.4,
  MetaTrader5 5.0.6180, terminal build 6191 (IC Markets Global), account ICMarketsSC-Demo
  (trade mode DEMO; login never recorded).
- **Branch:** `dashboard/responsive-live-windows` (local `dashboard-review`), built on
  `windows-validation`.

| Check | Command | Result |
|---|---|---|
| Byte-compile | `python -m compileall -q adaptive_scalper tests` | OK |
| Full suite | `python -m pytest -q -rs` | **1514 passed, 9 skipped, 0 failed** (skips: 8 opt-in live-MT5 tests, 1 symlink test -- non-admin Windows users cannot create symlinks) |
| Live MT5 DEMO tests | `ASN_LIVE_MT5=1 pytest tests/test_mt5_gateway_live.py` | **8 passed** (incl. the configured server clock matches live quotes) |
| Windows verifier | `scripts\windows_verify.ps1` | **RESULT: PASS** (`logs\windows_verify_20260925_135054.txt`) |
| Lint | `ruff check adaptive_scalper tests --select F,E9` (0.16.9) | all checks passed |
| Security | `bandit -r adaptive_scalper` (1.9.4) | 0 high, 7 medium, 3 low: the cloud-triaged false positives plus one new B608 in `history/time_basis.py` (table/column names from a constant tuple; values bound) |
| Dependencies | `pip-audit -r requirements.txt` (incl. MetaTrader5) | no known vulnerabilities |
| OKF | `okf validate` | valid |

Lint/security tools ran in a throwaway venv, not the project environment.

### Defects found and fixed on Windows

1. The offline suite launched the live MT5 terminal (a skip guard checked a module global
   that never exists); now blocked suite-wide, live tests opt-in. No order or order_check
   happened (temp databases inspected).
2. Broker server time (UTC+3 now, UTC+2 in winter) was treated as UTC everywhere
   (BUG_BACKLOG #14): central conversion in `Mt5Gateway`, startup clock check, one-shot
   backed-up conversion of the stored history (10 bars stamped inside the skipped spring
   hour quarantined, not deleted).
3. Configuration could raise every hard risk limit (up to 20 %); now refused by config and
   by `RiskLimits` itself.
4. A slow news HTTP fetch could stall the single-threaded scheduler (and DEMO's 1-s
   protective cycle) for the whole httpx timeout; the fetch now runs off-thread with a
   2-s wait. Measured on live PAPER: max scheduling lag 0.87 s.
5. Dashboard: a full integrity check on every refresh (6.1 s -> 0.05 s), negative ages,
   stale figures shown as current while the server was down, missing summary items,
   sidebar overflow at 150 %, a `"LIVE"` string that failed the no-LIVE-mode audit.
6. Windows symlink privilege test failure (split, honest skip).

### MT5 / live-data verification (no order sent)

- Account DEMO, terminal connected, terminal and account trading permitted (Algo Trading
  was already on). XAUUSD 'Gold vs US Dollar', GBPJPY, BTCUSD 'Bitcoin (USD)' by exact name;
  vol min/step 0.01, stops/freeze level 0, filling mode 2.
- Server clock: `tick.time - UTC` = +10800 s on all symbols; winter half confirmed from 212
  weeks of history; `doctor` and runtime startup report VERIFIED.
- `order_check` (never sent): retcode 0 "Done" (BUG_BACKLOG #5 closed).
- `reconcile`: CLEAN; 0 positions / 0 pending orders.
- `scan --source mt5`: the six strategies produced real signals and FLAT on live bars.

### Live PAPER (real MT5 data, simulated fills)

- ~6 h on the earlier build plus runs on each new build: 0 cycle failures, bars processed
  within seconds of close, dashboard beside it. **New entries blocked all along because the
  kill switch is UNINITIALIZED** (operator action pending), so no live simulated trade and no
  live restart with an open simulated position were possible.
- Restart correctness on real broker bars (deterministic replay, June 2026 XAUUSD, real
  costs): 5,975 restarts (102 with an open position, 96 with a pending entry) reproduce the
  continuous run exactly: 96 trades, 0 duplicates, identical equity.

### Historical research (broker history, not a forecast)

OOS holdout reserved and never run: **2026-07-01 .. 2026-09-18**. Design window results
(M5, real costs; no point-in-time news calendar exists for the past, so news blocking was not
applied):

| Symbol | Trades | Gross | Costs | Net | PF | PSR(>0) |
|---|---|---|---|---|---|---|
| XAUUSD 2025-06-01..2026-06-30 | 72 | +65 | 579 | -514 | 0.39 | 0.003 |
| GBPJPY 2025-06-01..2026-06-30 | 183 | +203 | 705 | -503 | 0.70 | 0.028 |
| BTCUSD 2025-10-01..2026-06-30 | 119 | +181 | 604 | -423 | 0.62 | 0.023 |

Walk-forward (`walk-forward --folds 5`, SEQUENTIAL_FIXED_CONFIG_EVALUATION, nothing re-fit):
**all 15 folds** lost 423-518 (each stopped by the 5 % drawdown halt). Aggregates: XAUUSD 452
trades PF 0.46, GBPJPY 575 trades PF 0.56, BTCUSD 478 trades PF 0.47; gross P/L -99 / +244 /
+252 against costs 2,386 / 2,735 / 2,706. Path stress (trade-order permutation of the
recorded runs: a drawdown distribution, terminal equity unchanged): p95 max drawdown
603 / 696 / 578, probability of ruin 0 (the halt caps losses).

Every run hit the 5 % drawdown halt within its first 2-4 days and was blocked for the rest
of the window (the halt works). `microstructure_acceleration` produced most trades (68/72,
160/183, 106/119) at negative average R. The strategies were not changed: this is evidence
for the operator's decision, not a tuning target.

### DEMO readiness

All automated gates pass (tests, DEMO account, clock, symbols, order_check, reconciliation,
risk ceilings, news, permissions, costs where evidenced). **Not started**: DEMO needs the
operator to bootstrap the kill switch; no DEMO order has been sent, and position management
on a real DEMO position is NOT VERIFIED. Based on the research above, expect frequent small
losing trades until the 2 % daily-loss or 5 % drawdown halt stops new entries.

---

## Cloud QA report (2026-09-24) -- historical

- **Runner:** Linux cloud container, Python 3.13.12, SQLite 3.45.1.
- **MT5:** `MetaTrader5` is not installable on Linux, and the cloud **never** connected to an
  MT5 terminal. Nothing below verifies broker behaviour.

## Results

| Check | Command | Result |
|---|---|---|
| Byte-compile | `python -m compileall -q adaptive_scalper tests` | OK |
| Full test suite | `python -m pytest -q -rs` | **1451 passed, 7 skipped, 0 failed** |
| Skipped tests | `tests/test_mt5_gateway_live.py` (7) | need the laptop's live MT5 terminal: BLOCKED-ON-LOCAL-MT5 |
| Lint (pyflakes + syntax) | `ruff check adaptive_scalper tests --select F,E9` (ruff 0.16.8) | all checks passed (20 older findings fixed) |
| Security: Bandit 1.9.4 | `bandit -r adaptive_scalper` | 0 high, 6 medium, 3 low; all triaged as false positives (below) |
| Dependencies: pip-audit 2.10.1 | `pip-audit -r requirements.txt` (MetaTrader5 excluded: Windows-only) | no known vulnerabilities |
| Secret scan | regex scan of every tracked file (credential assignments, private keys, cloud or GitHub tokens, account logins) plus a tracked-file name check | only synthetic test fixtures (fake logins, detector test strings); no `.env`, key, database or model files tracked |
| OKF bundle | `python -m adaptive_scalper.cli okf validate` | valid, 0 issues |
| Dashboard | TestClient, a real uvicorn process and headless Chromium | 15 panels, live WebSocket, no JS errors |

The security tools ran in a separate throwaway venv. They are **not** runtime dependencies.

## Bandit triage

| Finding | Where | Verdict |
|---|---|---|
| B608 SQL string construction ×6 | `backtest/persistence.py`, `execution/recovery.py`, `execution/store.py` ×2, `learning/observer.py`, `research/ledger.py` | False positive: the SQL is built from constant column names or `?` placeholder lists only; every value is bound as a parameter. |
| B311 `random` ×2 | `backtest/path_stress.py`, `knowledge/benchmark.py` | False positive: seeded simulation and benchmark RNGs, not security use. |
| B105 "hardcoded password" | `execution/request_token.py` (`'ASN:'`) | False positive: the order-comment request-token prefix. |
| B101 `assert` ×3 (fixed) | symbol validation, news blocking, exposure maps | Real: import-time invariants would vanish under `python -O`. Now explicit raises. |

## Audits that run in every test run

Each is enforced by an AST or source audit:
- **No LIVE or REAL mode strings:** `test_runtime_architecture.py`.
- **Retired-strategy firewall:** `test_strategy_registry.py`, `test_final_permission.py`,
  `test_learning_promotion.py`, `test_learning_jobs.py`, `test_knowledge.py`.
- **`order_send` and `order_check` call sites:** `test_runtime_architecture.py`,
  `test_architecture_execution_boundary.py`.
- **Gateway construction site:** `test_runtime_architecture.py`.
- **Kill-switch mutation** (bootstrap and clear only in `cli/operator.py`; the runtime never
  mutates it): `test_runtime_architecture.py`, `test_launchers.py`.
- **RAG, OKF and learning isolation:** `test_knowledge.py`, `test_rag_service.py`,
  `test_learning_structural_safety.py`, `test_runtime.py` (identical decisions with every
  advisory source raising).
- **Dashboard observer-only:** `test_dashboard_panels.py`.
- **SQLite migration from empty** (contiguous 1–26, integrity OK, idempotent):
  `test_persistence.py`.
- **Restart and persistence:**
  - `test_backtest_correctness_regressions.py` (PAPER chunked cycling equals one continuous
    run);
  - `test_paper_engine.py`;
  - `test_simulation_phase0.py`;
  - `test_position_management_state_store.py`;
  - `test_runtime_restart.py` (new DEMO engine over the same database and broker keeps
    managing the open position with no re-submission; a crash mid-submission is quarantined
    to UNKNOWN and blocks all new exposure; PAPER sessions resume exactly).
- **Fault injection:** `test_broker_chaos.py` (38 deterministic scenarios) and the runtime
  chaos tests in `test_runtime.py`.

## Not verified in the cloud

See `LOCAL_MT5_HANDOFF.md` → "Items the cloud could not verify", and BUG_BACKLOG items 5, 14, 17, 18
and 20.
