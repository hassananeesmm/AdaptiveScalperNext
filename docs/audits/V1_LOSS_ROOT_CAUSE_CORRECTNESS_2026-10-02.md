# V1 loss root-cause and correctness audit — 2026-10-02

Status: **correctness work only; no strategy rescue; no deployment**

Base: `release/0.2.7` (`5be15d8`)

This audit does not claim profitability. It separates defects that can make a
losing system worse from the separate question of whether any strategy has a
validated economic edge.

## Evidence already in the repository

`docs/research/INDEPENDENT_STRATEGY_RESEARCH_2026-09-27.md` reports that all
18 strategy/symbol sessions were negative after simulated costs. Several
strategies had small positive gross R, but no gross edge comfortably exceeded
friction. The live selector concentrated heavily in
`microstructure_acceleration`, whose `raw_confidence` is a heuristic score,
not an empirically calibrated probability.

Therefore the primary economic conclusion remains: **there is no validated
strategy edge in V1.** Correctness fixes below can prevent avoidable losses and
bad selection, but they cannot manufacture an edge.

## Finding C1 — pre-entry live cost gate undercharged per-fill slippage

Severity: **P0 correctness**

The shipped config describes `slippage_price` using observed individual
market-order fills. The simulator applies slippage on every fill. Before this
branch, `runtime.demo.live_cost_estimate()` passed that one-fill value once to
the selector/final-permission cost gate.

For a market entry followed by a market/stop exit, the expected lifecycle has
two slippage opportunities. The old pre-entry estimate was therefore:

`spread + round_trip_commission + 1 * per_fill_slippage + swap`

while the simulator effectively priced:

`spread + round_trip_commission + 2 * per_fill_slippage + swap`

before the uncertainty margin.

### Fix on this branch

`live_cost_estimate()` now explicitly means **pre-entry round-trip cost** and
charges `2 * slippage_price`. Regression tests lock this convention.

## Finding C2 — open-position expectancy reused the wrong cost horizon

Severity: **P0 correctness**

The same `live_cost_estimate()` was also used by `DemoRuntime._review()`.
That meant an already-open position was re-charged the full lifecycle spread,
round-trip commission and entry-side slippage when deciding whether the
remaining position still had positive expectancy.

Those entry costs are sunk. Re-charging them can make
`current_net_edge_price` too pessimistic and can trigger
`setup/thesis invalidated` exits earlier than intended.

### Fix on this branch

A separate `live_remaining_exit_cost_estimate()` now prices only remaining
friction:

- no additional current spread, because the review mark is already on the
  executable closing side of the quote;
- one expected exit slippage;
- one half of the configured round-trip commission;
- the configured conservative swap allowance.

`DemoRuntime._review()` now uses this remaining-cost estimate.

This is a correctness fix, not evidence that holding longer is profitable.
Forward counterfactual observation is still required.

## Finding C3 — raw_confidence is being used as P(win)

Severity: **P0 model semantics — unresolved**

`costs.edge.expected_gross_edge_price()` computes

`p * target_distance - (1-p) * stop_distance`

with `p = StrategySignal.raw_confidence`.

The six strategies do not produce the same kind of probability. Examples:

- momentum: regime confidence × efficiency ratio;
- pullback: regime confidence × a fixed discount;
- range breakout: regime confidence;
- statistical reversion: regime confidence × proximity term;
- volatility expansion: regime confidence × wick term;
- microstructure acceleration: `2 * abs(acceleration) / ATR`, capped at 1.

The independent study already found that the expected edge ranking did not
predict realized edge and that microstructure's score was badly miscalibrated.

### Required resolution

Do **not** fit another historical rescue mapping on the consumed H1-H9 evidence.
Collect prospective/shadow outcomes first. A future executable probability must
be strategy-specific and calibrated on data disjoint from the data used to fit
or choose the mapping. Until that evidence exists, `raw_confidence` should be
treated as a score, not a probability.

A later change should introduce a distinct typed field such as
`calibrated_win_probability`; the executable EV gate should fail closed when
that field is unavailable. That change is intentionally not mixed into this
cost-correctness patch because it changes the runtime from "can trade" to
"flat until calibrated" and requires a dedicated migration/test review.

Reference: scikit-learn probability calibration documentation:
https://scikit-learn.org/stable/modules/calibration.html

## Finding C4 — swap evidence semantics are incomplete

Severity: **P1 correctness — unresolved**

`SymbolCostConfig.swap_per_lot_per_day` defaults to `0.0`, although the
surrounding cost model says unknown costs must fail closed. For a short intraday
position this often has no economic effect, but a position that can cross the
broker rollover boundary needs explicit swap evidence.

Required resolution: make swap horizon-aware. If the maximum possible remaining
hold cannot cross rollover, incremental swap is zero for that decision. If it
can cross rollover, require broker-supported long/short swap evidence or block
the trade/hold assumption. Do not silently convert unknown swap to zero.

## Finding C5 — short-horizon strategy economics remain unfavorable

Severity: **fundamental research result, not a code bug**

The independent study reports that sub-5-minute microstructure trades were
strongly negative gross, while 5–15 minute subsets had only small positive gross
R that remained below costs. This is consistent with the general market
microstructure fact that high-turnover strategies are especially sensitive to
spread and execution costs.

This finding must not be "fixed" by post-hoc parameter tuning on the same
history. The H1-H9 strategy-search history is frozen.

References:
- https://academic.oup.com/rfs/article-abstract/29/1/104/1844518
- https://academic.oup.com/book/52292/chapter-abstract/421090015
- https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
- https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253

## Forward-only resolution path

1. Keep strategy research frozen; no H10/H9b rescue.
2. Do not deploy this branch automatically.
3. Run the full unit/integration suite and inspect CI before any merge.
4. Review the 13 local task-observer observations. They are not present in the
   repository snapshot used for this audit; classify them as safety,
   correctness, observability/tooling, documentation, or strategy/economic.
   Strategy/economic suggestions remain frozen.
5. Add prospective shadow logging for every candidate signal: strategy, raw
   score, regime, direction, spread, theoretical entry, MFE/MAE, stop/target
   outcomes, and fixed forward horizons. Shadow logging must not send orders.
6. After a preregistered minimum prospective sample, calibrate each strategy
   separately using time-ordered/out-of-fold evidence. Report reliability
   curves, Brier/log loss and sample counts.
7. Only then introduce `calibrated_win_probability` into executable EV.
   Missing/insufficient calibration must produce FLAT.
8. Keep conservative measured transaction costs. Do not lower slippage merely
   to make a strategy pass.
9. Use forward PAPER first; DEMO only after the corrected decision model has
   prospective evidence. REAL remains disabled.

## Acceptance criteria for this correctness branch

- Pre-entry cost includes two per-fill slippage allowances.
- Open-position review uses remaining, not sunk, transaction costs.
- Unknown slippage still fails closed.
- Existing hard risk ceilings, kill switch, broker truth, reconciliation,
  UNKNOWN quarantine, news fail-closed and DEMO-only execution are unchanged.
- No strategy parameter, regime threshold, stop/target multiple, risk ceiling,
  or REAL-mode control is changed.
- No release/main merge, runtime restart or deployment is performed by this
  work.
