# Cloud QA report (2026-09-24)

- **Runner:** Linux cloud container, Python 3.13.12, SQLite 3.45.1.
- **MT5:** `MetaTrader5` is not installable on Linux, and the cloud **never** connected to an
  MT5 terminal. Nothing below verifies broker behaviour.

## Results

| Check | Command | Result |
|---|---|---|
| Byte-compile | `python -m compileall -q adaptive_scalper tests` | OK |
| Full test suite | `python -m pytest -q -rs` | **1440 passed, 7 skipped, 0 failed** |
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

See `LOCAL_MT5_HANDOFF.md` → "Items the cloud could not verify", and BUG_BACKLOG items 5, 7,
14, 17, 18 and 20.
