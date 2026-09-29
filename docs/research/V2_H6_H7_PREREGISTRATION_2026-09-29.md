# Strategy research pre-registration, H6 and H7 (2026-09-29, evening)

**Status: PRE-REGISTERED before any H6/H7 code or run.** Research-only (BACKTEST evidence on
development data, research DB copy `data/research/v2_20260929.sqlite3`). Nothing here can
reach `order_send`. Reserved OOS 2026-07-01..2026-09-18 is never read.

## Why this generation (from `PROFITABILITY_ANALYSIS_2026-09-29.md`)

H1-H5 showed that the V1 entries have no gross edge at any holding horizon on M5, and that
losses concentrate where friction is large relative to the stop. The operator directed the
next generation at **slower, cost-efficient ENTRIES**, gross-edge screening first, before any
calibration. Exits are not the subject of this family.

Known before writing this: the H1-H5 results (all seen), the POST-HOC XAUUSD 12-16 UTC
segment (seen), and the first real DEMO stop-exit slippage evidence (XAUUSD p50 0.18 / p90
0.72, BTCUSD p50 0.94 / p90 5.70 per fill; small samples). No M15 result of any kind has been
looked at.

## Data (fixed; M15, native broker bars)

| Use | XAUUSD M15 | BTCUSD M15 |
|---|---|---|
| **H6 development** | 2024-06-01 .. 2026-06-30 | 2023-11-01 .. 2026-06-30 |
| **H7 holdout (H6 must never read it)** | 2022-06-23 .. 2024-05-31 | none |
| Reserved OOS (never) | 2026-07-01 .. 2026-09-18 | same |

12 sequential folds per symbol, each flat at 10,000; live risk limits unchanged (0.25 / 0.75
/ 2 / 5 %). Costs: the configured `BROKER_DEMO_CONFIRMED` fill assumptions are the decision
basis (unchanged). Reported alongside, clearly labelled and never used for a decision: a
DEMO-evidence cost scenario (entry slippage at the observed entry p90; stop exits at the
observed stop-exit p90).

Enforcement: the H6 runner refuses any XAUUSD M15 range that starts before 2024-06-01
(the H7 holdout), in addition to the OOS guards (request, counterfactual engine,
`run_backtest`).

## H6: slower, cost-efficient entries (gross-edge screen)

All entries fill at the next bar's open with the engine's own revalidation, sizing, cost and
fill model (`_revalidate_and_open`); exits replay with the H4 engine (`counterfactual.py`).
Stops and targets always stand; nothing widens a stop or adds size.

- **H6a, V1 entry logic on M15 (unchanged code).** The six frozen strategies and the live
  selector evaluated on M15 bars and features (their ATR-based stops and targets therefore
  widen with the resolution). 7 cohorts. Exits: `ST` (stop/target), `FH4` (1 h), `FH16` (4 h);
  the V1 exit is reported as a reference, not a trial. Trials: 7 x 3 x 2 = **42**.
- **H6b, cost-to-risk gate on the H6a entries.** Keep only entries whose estimated
  round-trip cost at the signal bar is <= 10 % or <= 5 % of the stop distance (both
  knowable at decision time). Exit `ST`. Trials: 2 x 7 x 2 = **28**.
- **H6c, one new, simple entry family: Donchian breakout.** Rationale: intraday
  time-series momentum / breakout persistence, the most documented simple trend effect,
  and one that needs wide stops (cost-efficient by construction). Rule at a closed M15 bar:
  BUY if the close is above the highest high of the previous N bars and the previous close
  was not; SELL symmetrically. Stop 2 x ATR, target 4 x ATR (ATR = the engine's causal
  `atr` feature), entry only if estimated round-trip cost <= 10 % of the stop. N in {20, 48}
  (5 h, 12 h). Exits `ST`, `FH16`. Trials: 2 x 2 x 2 = **8**.

**H6 total: 78 trials** (family `v2-H6:<symbol>`, DSR within the family).

**Screen (pre-registered, gross first):** a trial PASSES THE SCREEN only if
(1) the 95 % interval of mean gross R lies above 0, (2) mean gross R >= 3 x mean cost R,
(3) n >= 100. Screen passers then need the unchanged development criteria (net R > 0;
gross >= 1.5 x cost; >= 8/12 folds; PSR and DSR >= 0.95; PBO <= 0.20; positive at cost
x1.2) AND a causal, flat-only confirmation run (one counted trial each), because screen
entries may overlap in time.

## H7: XAUUSD London/New York overlap (POST-HOC origin, isolated)

Origin: found AFTER the fact in H4 (selector entries held to SL/TP, 12-16 UTC, n 170, net
+0.058 R on M5 development data). It is therefore not evidence; it may only be validated
on data nobody has looked at. No unseen M5 XAUUSD exists before the M5 development window,
so the only unseen history is the **XAUUSD M15 holdout 2022-06-23..2024-05-31**; the test is
a transfer to M15, stated as a limitation.

- **Trial H7-SESSION:** V1 selector entries on XAUUSD M15 in the holdout, restricted to
  fills 12:00-15:59 UTC, exit `ST`.
- **Control H7-ALL (reported, counted):** the same without the session restriction.

Trials: **2** (family `v2-H7:XAUUSD`). H7 runs once, only after H6 is complete, and nothing
about its window is inspected before then. Acceptance: the development criteria
(net R > 0, gross >= 1.5 x cost, >= 8/12 folds, PSR >= 0.95, n >= 100, positive at cost x1.2)
AND H7-SESSION net R above H7-ALL. A pass would still only justify forward confirmation on
new data (PAPER), never a lock by itself.

## Stopping rule

H6 and H7 each run exactly once (tag `v2r3`). No parameter outside the values above is tried.
If nothing passes: NO VALIDATED EDGE for slower V1 logic, the cost gate and the Donchian
family; the next step is a new pre-registered family (new economic rationale), not a
re-tune. All trials stay in the append-only ledger. OOS stays sealed.
