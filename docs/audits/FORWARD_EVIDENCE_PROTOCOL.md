# Forward evidence protocol and PAPER-eligibility criteria (PREREGISTERED 2026-10-03)

Committed before any shadow outcome exists. No criterion below may be lowered after an outcome is seen; a
change needs a new, dated version of this file committed **before** the data it is applied to, and every
variation tried counts as a trial in the research ledger (`data/research/v2_20260929.sqlite3`, 262 trials at
registration). Trial counts are never reset by a new branch.

## Data

- Source: `shadow_candidates` / `shadow_outcomes` (migration 0032), written by the DEMO runtime after the date
  this branch is deployed. Only rows with `observed_at_utc` after that deployment are post-freeze evidence.
- Excluded: anything in or derived from H1–H9 development data, and the sealed BTC OOS interval (SEALED —
  METADATA EXPOSED; PRICE/RETURN/OUTCOME DATA UNREAD). Any confirmatory dataset beyond shadow data needs explicit
  human approval of the exact range and protocol.
- Unit: one candidate per (symbol, strategy, decision bar, direction). Overlapping candidates of the same
  strategy and symbol within one horizon are **not independent**. Effective n is computed on non-overlapping
  decision bars (keep the first per horizon window).

## Calibration (only if a probability is used)

- Strategy- and symbol-specific; pooling only after a preregistered homogeneity test.
- Time-ordered, purged, out-of-fold (no shuffled CV), embargo ≥ one horizon.
- Method: Platt/sigmoid below 1,000 independent calibration observations; isotonic only at ≥ 1,000. The method is
  never chosen by trading P&L.
- Report: reliability diagram, bin counts, Brier, log loss, ECE, AUC (separately), confidence intervals and
  subgroup stability. If n is insufficient → `CALIBRATION_INSUFFICIENT_DATA` → FLAT.

## Executable EV

`EV = p · avg_realized_win − (1 − p) · avg_realized_loss − conservative_cost`. All three terms come from forward
evidence of the lifecycle actually followed (`EdgeEvidence` / `LifecyclePayoff`), never from configured
target/stop. A trade needs the **lower 95 % bound** of EV − cost > 0, not the point estimate.

## PAPER eligibility — all must hold, per strategy × symbol

1. ≥ 300 independent forward observations and ≥ 8 non-overlapping chronological blocks.
2. Mean gross R > 0 and mean net R > 0 (net = after the conservative cost below).
3. One-sided 95 % confidence bound of mean net R > 0 (block bootstrap over chronological blocks).
4. Net R > 0 in ≥ 6 of 8 chronological blocks; no single trade contributes > 10 % of total net R.
5. Cost stress: net R stays > 0 with all slippage × 1.5 and spread at its p75.
6. If p is used: calibration valid as above (ECE ≤ 0.05 on held-out folds, no subgroup ECE > 0.10 with n ≥ 50).
7. PSR(0) ≥ 0.95 and DSR ≥ 0.95, with the trial count = the full ledger count at evaluation time (≥ 262).
8. PBO ≤ 0.2 where several variants were compared.
9. Zero safety violations, zero forbidden data access, complete cost provenance (no UNVERIFIED cost component).

Outcome labels: all hold → `FORWARD POSITIVE NET EDGE VALIDATED — PAPER ELIGIBLE — HUMAN APPROVAL REQUIRED`;
n or blocks not reached → `FORWARD EVIDENCE INSUFFICIENT — SYSTEM REMAINS FLAT`; criteria evaluated and failed →
`FORWARD EDGE REJECTED — SYSTEM REMAINS FLAT`.

## Promotion ladder

CORRECTNESS VERIFIED → SHADOW → forward PAPER → controlled DEMO → human review → (separate, out-of-scope REAL
decision). No automatic promotion at any step.
