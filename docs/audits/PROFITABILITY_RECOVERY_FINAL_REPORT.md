# Profitability recovery — final report (2026-10-03)

**Terminal state: B — CORRECTNESS VERIFIED — NO VALIDATED EDGE — SHADOW COLLECTION READY (not yet active:
it starts only when this branch is deployed, which needs operator approval).**

The evidence supports: **NO VALIDATED POSITIVE NET EDGE.**

## 1. Repository / commit state

- Work branch: `fix/v1-loss-root-cause-correctness` (draft PR #7), worktree `.worktrees/profit-recovery`.
- Base `release/0.2.7` (`5be15d8`); PR #7 head at start `3834d93`.
- Commits added:
  - `7ba9dce` — typed cost horizons, horizon-aware swap, PAPER BLOCK_COST, fail-closed edge evidence (issues #6, #8).
  - `1ee1f65` — shadow observer (migration 0032) and strategy lifecycle contract.
  - the documentation commit that adds this report — audit documents, read-only audit scripts, status/worklog/backlog.
- Not merged into `release/*` or `main`. `release/0.2.8` (`ed15d76`, microstructure entry suspension) is a sibling
  of this branch: both start from `5be15d8`, and this work does not contain it. Integrating the two is a separate
  reviewed step.
- Protected history untouched: `release/0.2.7`, `release/0.2.8`, `main`, H8/H9 branches and tags, research ledger,
  OOS incident history.

## 2. Runtime state

- On 2026-10-02 the 0.2.7 DEMO runtime (`d9c1bd1`, schema 31) last heartbeat at 17:25:35 UTC. It ended without an
  ENGINE_STOPPED event; no runtime, dashboard or MT5 process was running when this audit started. That is consistent
  with a machine restart, which this session did not cause.
- Last recorded state (read-only): trade mode DEMO, balance = equity 9,473.85 USD, 0 open positions, 0 working orders,
  0 unresolved incidents, close requests all resolved (151 closed, 1 not executed), reconciliation CLEAN, kill switch
  DISENGAGED (startup record), schema 31, quick_check ok.

## 3. Deployed runtime changed? **NO**

No restart, no deployment, no kill-switch change, no configuration change on the live install, no database write.
The production database was only ever opened `mode=ro`.

## 4. Complete root-cause matrix

See `docs/audits/PROFITABILITY_ROOT_CAUSE_FINAL.md` §4 (D1–D13 defects, E1–E3 economic failures,
H1–H2 hypotheses, I1–I2 insufficient evidence, L1 not demonstrated).

## 5. Confirmed defects (summary)

D1/D2 (PR #7, DEMO cost horizon) · **D3** backtest/PAPER cost horizon (unfixed by PR #7) · **D4** raw score
used as P(win) · **D5** holding thesis = entry trigger · **D6** selector payoff ≠ runtime lifecycle ·
**D7** review "remaining edge" ignores the stop · **D8** unknown swap = 0 · **D9** UTC vs server rollover ·
**D10** PAPER free fills on unknown cost · **D11** horizon field without authority · **D12** `p=` display ·
**D13** threshold-cross time never recorded.

## 6. Economic failures

E1 no gross edge exceeds cost (18/18 sessions net-negative); E2 microstructure < 5 min strongly negative gross;
E3 selection dominated by an uncalibrated score. DEMO: 408 positions, −234.00 USD.

## 7. Corrected code paths

| Path | Before | After |
|---|---|---|
| `costs/model.py` | one untyped estimate | `horizon` tag; per-fill builders `round_trip_cost_from_evidence` (2 fills) / `remaining_exit_cost_from_evidence` (1 fill, exit half commission, no sunk cost) |
| `costs/swap_horizon.py` (new) | swap = configured value (default 0) always | server-midnight rollover; 0 only if the max hold cannot cross; unknown + crossing → None (BLOCK_COST) |
| `runtime/demo.py` | PR #7 per-fill fix | builders + swap horizon + `require_horizon`; edge evidence provider; shadow hook |
| `backtest/engine.py` (PAPER uses it) | `_estimate_cost` 1× slippage everywhere; review full round trip from mid | `_entry_cost` / `_remaining_exit_cost` identical to DEMO; review from executable mark; realized swap per server rollover; unknown swap crossed → invariant error |
| `costs/edge.py`, `selector/selector.py`, `core/final_permission.py` | `p = raw_confidence` | `EdgeEvidence` only; default NONE → `BLOCK_EDGE_UNVALIDATED`; final permission VALIDATED-only |
| `costs/edge_evidence.py` (new) | — | `CalibratedWinProbability`, `LifecyclePayoff`, providers; legacy V1 replay labelled and refused by DEMO/PAPER |
| `runtime/paper.py` | unknown cost → 0 | BLOCK_COST entries; vetted edge provider; server-time rule |
| `simulation/fill_model.py` | v2 | **v3** (persisted semantics changed → old PAPER sessions refuse to resume) |
| `shadow/observer.py`, migration `0032` (new) | — | append-only candidate + outcome evidence, no order path |
| `shadow/lifecycle.py` (new) | — | seven-part lifecycle contract; V1 recorded as THESIS_COUPLED_TO_ENTRY_TRIGGER |

Unchanged: risk ceilings (0.25 / 0.75 / 2 / 5 %, 2 positions, 1 per symbol), `calculate_safe_volume`, kill switch,
broker-truth, reconciliation, UNKNOWN quarantine, durable close requests, news fail-closed, DEMO verification,
symbol and terminal identity, the six V1 strategy files (digests unchanged), executable exit rules.

## 8. Test evidence (local Windows, canonical `.venv`, `ASN_DISABLE_MT5=1`)

| Tree | compileall | Result |
|---|---|---|
| PR #7 head `3834d93` (baseline) | OK | 1801 passed, 1 failed, 9 skipped (1531 s). The failure was the V1 freeze digest of `selector.py`, caused by this session editing that file **while the run was in flight**. The committed HEAD's selector digest equals the pin, so the effective baseline is 1802 / 0 / 9. |
| `7ba9dce` | OK | **1841 passed, 0 failed, 9 skipped** (588 s) |
| `1ee1f65` | OK | **1858 passed, 0 failed, 9 skipped** (721 s) |

Skips (9): 8 live-MT5 tests (opt-in `ASN_LIVE_MT5=1`), 1 symlink test (OS privilege).
Lint: `ruff check --select F,E9` on every changed file — clean (two unused imports created by this work were removed).
Mutation / negative controls (all killed):
- one-fill slippage (pre-PR-7 convention) → 4 failures;
- review recharging the whole round trip → 3 failures;
- unknown swap silently 0 → 3 failures;
- AST raw-score scan flags the exact pre-fix V1 formula;
- the V1 legacy replay is numerically identical to the frozen selector over 500 randomized candidate sets (this is what justifies re-pinning `selector.py`).

Required negative-control coverage (prompt section 24):

| Requirement | Test |
|---|---|
| removing exit slippage fails | `test_runtime_cost_semantics.py::test_negative_control_one_fill_slippage_underestimates_the_round_trip` + mutation 1 |
| raw_score cannot enter probability EV | `test_cost_edge.py::test_raw_score_cannot_enter_the_executable_ev_gate`, `test_edge_evidence_isolation.py` (AST, with negative control) |
| no sunk entry cost in remaining review | `test_runtime_cost_semantics.py::test_open_position_review_never_recharges_sunk_entry_costs`, DEMO == backtest review test, mutation 2 |
| trigger disappearing ≠ thesis invalid | `test_strategy_lifecycle.py::test_an_entry_trigger_disappearing_does_not_invalidate_an_explicit_thesis` (+ characterization pin of the V1 executable defect) |
| unknown rollover cost fails closed | `test_runtime_cost_semantics.py::test_unknown_swap_is_zero_only_when_the_horizon_cannot_cross_rollover`, `test_backtest_entry_blocks_when_unknown_swap_can_cross_rollover`, mutation 3 |
| shadow observer cannot call order_send | `test_shadow_observer.py::test_the_shadow_package_cannot_reach_an_order_path`, end-to-end `order_send == 0` |
| learning/calibration cannot alter risk limits / enable REAL | `test_edge_evidence_isolation.py::test_edge_evidence_module_cannot_reach_risk_kill_switch_gateway_or_execution` (+ existing `test_learning_structural_safety.py`) |
| OOS ranges cannot enter training/calibration | existing `test_backtest_oos.py`, `test_backtest_correctness_regressions.py::test_oos_refuses_overlap_*` (unchanged, passing). No calibration code exists yet that could read OOS. |
| no strategy can bypass final permission | `test_final_permission.py::test_edge_gate_*`, existing `test_architecture_execution_boundary.py` (unchanged, passing) |

## 9. Transaction-cost evidence

Configured per-fill slippage (`[costs.*]`): XAUUSD 0.41 (p90, n=196 entry fills), BTCUSD 11.97 (p90, n=141),
GBPJPY unknown (BLOCK_COST). DEMO exit-side FINAL observations: BTC stop-loss n=68 mean 1.03; XAU stop-loss
n=38 mean 0.15; agent closes n=13 / 15 with favourable mean slippage; 1 take-profit. The configured BTC figure is
roughly 10× the observed stop-loss mean. It is deliberately **not** lowered: exit tails at n=68 do not support a
p90, and lowering costs to make a strategy pass is forbidden. Cost model v2 (separate distributions per fill kind,
conservative quantiles once n supports them) remains forward work.

## 10. Exit-lifecycle findings

TP reached once in 408 DEMO positions. The realized payoff is set by the adaptive exits (giveback, 1 R early TP,
"thesis invalidated", stop). 161 positions were closed one bar after entry because the event trigger stopped
firing (−0.209 R mean). The selector priced trades with a payoff the runtime does not follow (D6). Fixed for the
executable EV (needs a realized `LifecyclePayoff`). Exit rules themselves were not changed (first measure).

## 11. Raw-score / calibration findings

The raw score is a different heuristic per strategy; realized hit rate is flat across raw-score buckets; the
expected-edge ranking does not predict realized edge. `CALIBRATION_INSUFFICIENT_DATA`: there are no
post-freeze, prospectively recorded outcomes yet. No mapping from raw score to probability was created.

## 12. Shadow observer status

Implemented and tested (`shadow/observer.py`, migration 0032). It runs inside the DEMO runtime on every new closed
bar, including under global blocks (candidates recorded NOT_EVALUATED). Horizons 5/10/15/30 min resolve on M5;
1/3 min are recorded as UNSUPPORTED_RESOLUTION. Not active: it starts only when this branch is deployed
(operator approval; migration 0032 is additive).

## 13. Task-observer review (13 observations)

| # | Title (short) | Class | Disposition |
|---|---|---|---|
| 0001 | pinned workspace path nesting ambiguity | TOOLING | proposal only |
| 0002 | Windows cp1252 truncation on scripted edit | TOOLING | applied in practice this session (all scripted edits used explicit UTF-8) |
| 0003 | GateGuard first-touch gate scales with file count | TOOLING | proposal only |
| 0004 | verify script side effects before an approval plan | SAFETY (operations) | stands; relevant to any deployment of this branch (`STOP TRADING.bat` engages the kill switch) |
| 0005 | throwaway Postgres for race negative controls | TOOLING (other project) | not applicable here |
| 0006 | long-running services must be owned by the operator's launcher | SAFETY (operations) | stands; any restart must be started by the operator |
| 0007 | later migration silently reverts a parallel branch | CORRECTNESS (migrations) | applied: every branch checked, 0032 unused before adding it |
| 0008 | stacked-PR retarget needs a CI re-run | TOOLING | proposal only |
| 0009 | hosted migration history vs actual schema | CORRECTNESS (other project) | not applicable here |
| 0010 | GateGuard partial parallel batch | TOOLING | recurred twice this session; appended to the observation |
| 0011 | check for concurrent agent sessions | TOOLING / SAFETY | applied at session start |
| 0012 | verify data coverage per resolution before preregistering | STRATEGY/ECONOMIC (process) | applies to any future preregistration; no historical tuning reopened |
| 0013 | sealed-holdout guards must cover ad-hoc queries | SAFETY (research integrity) | applied: one unsafe ad-hoc query was caught before returning data and removed |

New this session: 0014 (editing source during a long test run contaminates the baseline). No strategy or economic
suggestion reopened historical tuning.

## 14. Historical / OOS integrity

H1–H9 frozen; H9 REJECTED; the research ledger (`data/research/v2_20260929.sqlite3`, 262 trials) was read but not
written. BTC OOS: **SEALED — METADATA EXPOSED; PRICE/RETURN/OUTCOME DATA UNREAD**, not accessed this session.
The preregistered correctness replay (section 18) was prepared, not run:
`docs/audits/CORRECTNESS_REPLAY_PREREGISTRATION.md`.

## 15. Remaining unknowns

Calibrated P(win) (I1); exit-cost tails (I2); whether a decoupled thesis improves realized R (H1); whether the
5–15 min gross is real (H2); threshold→decision latency (D13); triple-swap weekday rule (swap is unknown anyway,
so crossing decisions block).

## 16. Forward evidence requirements

Preregistered PAPER-eligibility criteria are in `docs/audits/FORWARD_EVIDENCE_PROTOCOL.md`. They must be committed
before any outcome is examined and may not be lowered afterwards.

## 17. Exact next human decision

1. Review and, if approved, merge PR #7 into a release branch (it does not include 0.2.8's suspension; integrate
   both).
2. Approve a flat, operator-started controlled restart onto that release, so the shadow observer begins collecting.
   The runtime will be FLAT by design (BLOCK_EDGE_UNVALIDATED).
3. Decide whether to spend trial budget on the preregistered correctness replay. Recommendation: not now; it
   cannot change the terminal state.

No REAL trading is enabled or proposed. Future profitability is not implied by anything in this report.
