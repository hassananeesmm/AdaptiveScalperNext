# Integrated forward-edge FLAT/SHADOW release candidate — audit (2026-10-03)

**Status: NO VALIDATED NET EDGE — FLAT.** Release candidate prepared for human review. **Not merged, not deployed.**

| | |
|---|---|
| Branch | `fix/integrated-forward-edge-shadow` (worktree `.worktrees/integrated-fes`) |
| Common base | `release/0.2.7` `5be15d8` |
| Sources | PR #7 `fix/v1-loss-root-cause-correctness` `55f6d53` + `release/0.2.8` `ed15d76` |
| Head | code `0cbc31b` (+ the documentation commit that adds this file) |
| Deployed runtime | 0.2.7 `d9c1bd1`, **unchanged** (no restart, no kill-switch change, production DB opened read-only only) |
| REAL | disabled; no REAL code path touched |
| `release/*`, `main` | untouched |

## 1. Did this task prove the bot profitable?

**NO.** Nothing here is evidence of edge. No historical strategy search, tuning, OOS access or replay was run.

## 2. Did it create a trustworthy mechanism for discovering whether a positive net edge exists prospectively?

**YES, as implemented and tested, with the limits in §11.** Forward candidates and their fixed-horizon and lifecycle
outcomes are recorded append-only, without lookahead, by code that cannot reach a broker mutation. The lifecycle
outcome is produced by the same exit code the backtest/PAPER engine runs, proven by an equivalence test. No evidence
can authorize an order unless it carries a sealed certificate that satisfies the current preregistered protocol.
The validation pipeline that would issue such a certificate does **not** exist yet. That is deliberate: until it
exists, and until its forward data passes the protocol, nothing can trade.

## 3. Integration

- Merge commit `3c0dc53`: both branches start from `5be15d8`. All four conflicts (`backtest/types.py`,
  `runtime/paper.py`, `runtime/demo.py`, `WORKLOG.md`) were "both sides added" and keep both sides.
- 0.2.8's suspension test controls were made explicit. The DEMO control now gives **every** signal verified fixture
  evidence and still never trades the suspended strategy.
- Post-merge cleanup: one duplicated import left by the merge was removed (ruff F811).

## 4. Economic-semantic changes in this candidate (relative to deployed 0.2.7)

| Area | 0.2.7 | Candidate |
|---|---|---|
| Expected edge | `raw_confidence · target − (1 − raw_confidence) · stop` | only from VALIDATED `EdgeEvidence` carrying a sealed, protocol-satisfying certificate; default none → **FLAT** |
| Cost horizon | DEMO fixed in PR #7; backtest/PAPER undercharged slippage and recharged sunk costs | typed `FULL_ROUND_TRIP` / `REMAINING_EXIT`, per-fill builders, DEMO == backtest (tested) |
| Swap | unknown = 0 | horizon-aware at broker server midnight; unknown + can cross → BLOCK_COST |
| PAPER unknown cost | simulated as free | BLOCK_COST |
| Microstructure | executable (0.2.7); suspended before selector (0.2.8) | suspended before selector **and** blocked in final permission (`BLOCK_STRATEGY_SUSPENDED`), even with verified evidence |
| Fill model | v2 | **v3** (old PAPER sessions refuse to resume) |
| Shadow evidence | none | candidates + fixed horizons + counterfactual lifecycle (migration 0032) |

Unchanged: all risk ceilings (0.25 / 0.75 / 2 / 5 %, 2 positions, 1 per symbol), `calculate_safe_volume`, kill switch,
broker truth, reconciliation, UNKNOWN quarantine, duplicate protection, news fail-closed, DEMO verification, symbol
and terminal identity, every V1 strategy file (digests unchanged), and executable exit rules.

## 5. Validation certificate (`adaptive_scalper/validation/certificate.py`)

- **Shape.** An immutable `ValidationCertificate` with: schema; protocol version; strategy key and version; symbol;
  direction (or ANY); lifecycle id and version; horizon; model id, version and calibration method; training,
  calibration and validation intervals; as-of and expiry; raw and effective observations; chronological and positive
  blocks; Brier, log loss and ECE; calibration result; mean gross and net R; net-R lower 95 % bound; average realized
  win and loss (R); max single-trade share; cost-model version; cost provenance; cost-stress net R; ledger trial
  count; PSR, DSR and PBO; safety result; forbidden-data flag.
- **Seal.** *(Superseded 2026-10-03 by Ed25519 public-key signatures, `edge_certificate/v2`; see
  docs/audits/FINAL_FLAT_SHADOW_RELEASE_HARDENING.md. Kept as the historical record of `0cbc31b`.)*
  HMAC-SHA256 over canonical JSON with `allow_nan=False`. The key lives in a file outside Git
  (`ASN_EDGE_CERTIFICATE_KEY_FILE`, at least 32 bytes). No key means nothing verifies (FLAT). Only `validation/` may
  seal (AST test).
- **Verification at the moment of use** (final permission → `executable_edge_check` → `verify_certificate`). The
  seal is checked first, then every field again against `CURRENT_PROTOCOL`:
  - binding: strategy, version, symbol, direction, lifecycle version, horizon, and the proposal's own stop distance;
  - time: chronological intervals, evidence newer than the 2026-10-03 research freeze, no overlap with the sealed OOS
    interval, not in the future, not expired;
  - protocol: n_eff ≥ 300; ≥ 8 blocks with ≥ 6 positive; gross, net and lower-bound net all > 0; cost stress > 0;
    single trade ≤ 10 %; BROKER_DEMO_CONFIRMED costs; current fill-model version; calibration passed with ECE ≤ 0.05;
    PSR and DSR ≥ 0.95; PBO ≤ 0.20; trial count ≥ 262; safety passed; no forbidden data.
  - Every failure returns a named reason. `ProtocolThresholds` can only be constructed in `certificate.py` (AST test).
- **Expected edge.** `EV_price = stop × (p · avg_realized_win_r − (1 − p) · avg_realized_loss_r)`. The probability
  must come from the certificate's own model and version.

## 6. Shadow lifecycle (`adaptive_scalper/shadow/`)

- **Candidates.** Every candidate of every active strategy is recorded per new closed bar, including those that are
  suspended (REJECTED `strategy_entry_suspended`), lack evidence (REJECTED `no_validated_edge_evidence`), or sit under
  a global block (NOT_EVALUATED). Rows carry causal features, regime, session, news, spread, ATR, movement-to-cost,
  stop/target, horizon, full round-trip cost and provenance, scheduler lag, selector result, final-permission result
  (`NOT_REACHED` / `DECIDED_IN_ENTRY_DECISIONS` plus `chain_key`) and the edge model. `raw_score_is_probability` is
  CHECK-constrained to 0.
- **Fixed horizons.** 5/10/15/30 min are resolved on M5; 1/3 min are recorded as `UNSUPPORTED_RESOLUTION`. A row is
  written only after its horizon has closed.
- **Counterfactual lifecycle (`lifecycle_counterfactual.py`).** A nominal market fill at the causal fill bar, then per
  closed bar the same `fill_pending_exit` / `manage_open_trade` functions `run_backtest` and PAPER call. Those call the
  DEMO manager's pure core (`evaluate_position_expectancy`, `evaluate_adaptive_exit`, `resolve_new_stop_price`), so
  every exit is covered: broker stop/target, +1 R, giveback, breakeven, thesis invalidation, regime reversal,
  remaining edge and max hold. The regime tracker is warmed exactly as `run_backtest` warms it. Recorded per
  candidate: entry, stop, target, 1 R distance, MFE/MAE and their times, exit time/price/reason, gross / cost / net R
  (net is NULL when any cost is unknown), holding time, cost provenance and lifecycle version.
- **Equivalence proof.** For every trade `run_backtest` makes over three random-walk seeds (with at least three exit
  kinds across five seeds), the evaluator reproduces the same exit bar, reason, price, gross R and net R. A negative
  control with a different exit policy breaks the equivalence.
- **Isolation.** A transitive import walk from `adaptive_scalper.shadow` never reaches `execution`, the MT5 gateway or
  its factory/synchronizer, and no module on that path calls `order_send`, `order_check`, `submit_new_entry`,
  `close_position`, `modify_stop` or `position_close`. End to end: `order_send == order_check == 0`.
  - Gap: the shadow observer runs inside the DEMO runtime process, which itself can send orders. Isolation is at the
    module level, not the process level (§11).

## 7. Cost-horizon verification

| Producer / consumer | Horizon | Units |
|---|---|---|
| DEMO `live_cost_estimate` → selector, final permission, shadow | FULL_ROUND_TRIP: spread once + 2 × per-fill slippage + round-trip commission + swap if a rollover can be crossed | price |
| DEMO `_review` | REMAINING_EXIT: exit slippage + ½ commission + remaining-horizon swap; mark on the executable side, no spread | price |
| backtest/PAPER `_entry_cost` (selector + fill-bar revalidation) | FULL_ROUND_TRIP (equal to DEMO for equal evidence, tested) | price |
| backtest/PAPER `_remaining_exit_cost` (review) | REMAINING_EXIT from `_mark_price` | price |
| fill simulation (`simulate_fill`) entry / market / stop exits | half spread + per-fill slippage per fill (stop gap-through at the open) | price |
| take-profit exit | limit at target, no slippage | price |
| realized `_close_trade` | prices include spread and slippage; commission + swap per server rollover charged once | money → R |
| shadow fixed horizon | gross mid-to-mid R; net = gross − full round-trip cost / stop | R |
| shadow lifecycle | the same `_close_trade` as the backtest | R |

Config units: `slippage_price` is price per fill; `commission_per_lot_round_trip` is money per lot per round trip;
`swap_per_lot_per_day` is money per lot per rollover (None means unknown).

## 8. Swap verification

The rollover is broker server midnight under `[mt5] server_time_rule`. Zero is used only when the maximum hold
cannot cross it: DEMO max hold + 60 s, backtest max hold + one bar. Unknown swap with a possible crossing returns
None, which blocks with BLOCK_COST. Realized backtest swap is charged per server rollover crossed. In the
counterfactual lifecycle, an unknown swap actually crossed leaves net R NULL. All of this is tested.

## 9. Required negative controls

| # | Control | Test |
|---|---|---|
| 1 | raw score cannot authorize EV | `test_cost_edge.py::test_raw_score_cannot_enter_the_executable_ev_gate`; `test_edge_evidence_isolation.py` (AST taint scan + negative control) |
| 2 | missing evidence blocks | `test_cost_edge.py::test_no_evidence_is_block_edge_unvalidated_even_with_a_perfect_raw_score`; `test_edge_evidence_isolation.py::test_demo_runtime_is_flat_by_default_and_never_sends` |
| 3 | forged/unverified VALIDATED blocks | `test_validation_certificate.py::test_unsealed_*`, `test_certificate_sealed_with_another_key_*`, `test_tampering_*`, `test_a_bare_validated_status_*` |
| 4 | expired blocks | `test_validation_certificate.py::test_expired_certificate_is_refused` + final-permission end-to-end |
| 5 | wrong strategy/version blocks | `test_validation_certificate.py::test_wrong_strategy_version_or_symbol_is_refused` + end-to-end |
| 6 | wrong symbol blocks | same parametrized test |
| 7 | insufficient sample blocks | `test_every_protocol_failure_fails_closed[INSUFFICIENT_SAMPLE]` + end-to-end |
| 8 | unverified cost blocks | `test_every_protocol_failure_fails_closed[UNVERIFIED_COST_EVIDENCE]` + end-to-end |
| 9 | one-fill slippage ≠ round trip | `test_runtime_cost_semantics.py::test_negative_control_one_fill_slippage_underestimates_the_round_trip` (+ mutation) |
| 10 | no sunk-cost recharge | `test_runtime_cost_semantics.py::test_open_position_review_never_recharges_sunk_entry_costs` (+ mutation) |
| 11 | unknown required swap blocks | `test_runtime_cost_semantics.py::test_unknown_swap_is_zero_only_when_the_horizon_cannot_cross_rollover` |
| 12–14 | shadow cannot send / close / modify stops | `test_shadow_lifecycle.py::test_nothing_the_shadow_package_imports_transitively_reaches_a_broker_mutation_boundary`, `test_shadow_observer.py::test_the_shadow_package_cannot_reach_an_order_path` |
| 15 | microstructure non-executable | `test_integrated_release_controls.py` (final permission with verified evidence; DEMO with verified evidence for every signal); `test_entry_suspension.py` |
| 16–17 | learning/calibration cannot alter risk or enable REAL | `test_validation_certificate.py::test_validation_package_cannot_reach_*`, `test_edge_evidence_isolation.py::test_edge_evidence_module_cannot_reach_*`, existing `test_learning_structural_safety.py` |
| 18 | no strategy bypasses final permission | existing `test_architecture_execution_boundary.py`; `test_final_permission.py::test_edge_gate_*` |
| 19 | migration 31 → 32 | `test_integrated_release_controls.py::test_migration_31_to_32_on_a_populated_database` + production-copy rehearsal (§10) |
| 20 | fresh DB | `test_integrated_release_controls.py::test_a_fresh_database_migrates_to_32_with_append_only_shadow_tables` |
| 21 | incompatible PAPER fingerprint | `test_integrated_release_controls.py::test_a_paper_session_from_the_previous_fill_model_is_refused`; `test_simulation_phase0.py::test_resuming_a_paper_session_under_a_different_config_fails_closed` |
| 22 | kill switch fail-closed | `test_integrated_release_controls.py::test_kill_switch_blocks_*`, `test_uninitialized_kill_switch_*` (+ existing kill-switch suite) |

## 10. Test totals and migration verification

- compileall: OK.
- Full suite (local Windows, canonical `.venv`, `ASN_DISABLE_MT5=1`, exact head): **1936 passed, 0 failed, 9 skipped, 2 warnings** (1580 s, tree = `0cbc31b`). The 9 skips are
  8 live-MT5 opt-in tests and 1 symlink-privilege test.
- Lint: `ruff check --select F,E9` clean on every changed file.
- Migration rehearsal on an online backup of the production DB (read-only source; copy deleted afterwards): schema
  31 → 32 in 0.01 s; quick_check ok before and after; 0 foreign-key violations; 40 non-sealed tables compared, with
  the only row change in `schema_migrations` (+1); positions 408, orders 914 and deals 816 unchanged. The bar/tick
  tables, which contain the sealed OOS interval, were not queried.

## 11. Remaining defects and limits (evidenced)

- No validation pipeline exists to issue certificates, so the candidate cannot trade by construction. The pipeline
  must be built, reviewed and its key provisioned before PAPER eligibility can even be evaluated.
- **ASN-028 (unchanged executably):** V1 holding thesis = the entry trigger re-run. The counterfactual lifecycle
  measures the current behaviour; a decoupled thesis needs preregistered forward evidence.
- **Process-level isolation.** The shadow observer shares the DEMO runtime process. Module-level isolation is proven;
  a separate read-only shadow process would make it physical (proposal, not done).
- Counterfactual warm-up recomputes features over the available history per candidate, which costs O(history) per
  candidate. That is fine at M5 cadence and is the reason the lifecycle tests take about 5 minutes.
- Lifecycle counterfactuals use bar data: same-bar stop/target ambiguity resolves to the stop and there are no ticks.
  This is consistent with the backtest, but it is a modelling limit.
- ASN-029 (`threshold_cross_at_utc` never written) and ASN-030 (exit-cost tails n too small) still stand.
- Migration 0032 was extended in place on this branch (final-permission result, chain key, lifecycle table) because
  it had never been released or deployed. Any scratch database that applied the earlier 0032 must be recreated.

## 12. Task-observer review

Observations live outside the repository (`~/.claude/skill-observations/observation-log/`) and were available.
0001–0013 are classified in docs/audits/PROFITABILITY_RECOVERY_FINAL_REPORT.md §13 (TOOLING 0001/0002/0003/0005/0008/0010,
SAFETY 0004/0006/0013, CORRECTNESS 0007/0009, TOOLING/SAFETY 0011, STRATEGY/ECONOMIC-process 0012). New since then:
0014 (TOOLING: freeze the tree during long test runs), applied in this task. 0010 recurred several times in this task
(blocked first edit of a parallel batch) and is recorded. No strategy/economic item reopened historical mining.

## 13. Deployment / runtime / REAL

- Deployment status: **NOT DEPLOYED.**
- Runtime: 0.2.7 `d9c1bd1`, not running since 2026-10-02 17:25 UTC (stopped before this work), flat, untouched.
- REAL: disabled.

## 14. Exact next human decisions

1. Review this branch, open it as a PR, and approve or reject it. It supersedes the pieces of PR #7 and 0.2.8 it
   contains.
2. If approved: release build, verified backup, migration 0032 on that backup, then a **flat, operator-started**
   restart. The runtime stays FLAT and begins shadow collection.
3. Separately, and later: commission and review a certificate-issuing validation pipeline, and provision its key
   outside Git. Only forward evidence that passes the preregistered protocol can ever produce a certificate.
