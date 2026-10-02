# Strategy V2 research pre-registration, H4 and H5 (2026-09-29)

**Status: PRE-REGISTERED before any H4/H5 run.** Research-only (BACKTEST evidence on
development data, research DB copy). Nothing here can reach `order_send`.

## Disclosure of what was already seen

H1-H3 (`docs/research/V2_PREREGISTRATION_2026-09-29.md`) were run once as tag `v2r1`
(2026-09-29 07:05-07:45 local). Before writing this document, the author had seen the headline
v2r1 numbers: across all six V1 strategies and both symbols, mean GROSS R per trade under the
V1 exit is roughly -0.08..+0.06, mean cost R is 0.11..0.25, every session is net-negative,
H1/H2 barely move any number, and H3 abstains on every bar (0 trades). H4 and H5 were
specified by the operator's master prompt (sections 21-24) independently of those numbers;
the concrete parameter values below (the fixed-hold lengths) were chosen by the author after
seeing v2r1 and are therefore declared here as the ONLY values that will be run. No other
values will be tried in this family. Anything added later is labelled POST-HOC and counted.

## Data and protocol (fixed, identical to H1-H3)

- Research DB: `data/research/v2_20260929.sqlite3` (a copy of the 2026-09-27 snapshot; the
  trial ledger is append-only by trigger). Never the production DB.
- BTCUSD 2025-10-01..2026-06-30, XAUUSD 2025-06-01..2026-06-30, M5, 12 sequential folds
  (`fold_index_ranges`), each fold starting flat at 10,000 with the live risk limits
  (0.25 / 0.75 / 2 / 5 %). Costs: the configured `BROKER_DEMO_CONFIRMED` fill assumptions.
  GBPJPY excluded.
- **Protected OOS 2026-07-01..2026-09-18 is not read.** Guards: the study, the counterfactual
  engine AND `run_backtest` itself refuse any range that touches it while a research hook is
  supplied (`adaptive_scalper/backtest/reserved_oos.py`).
- The V1 baseline is re-run fresh on the same bars (source of truth for this study) with its
  candidate log, so each V1 entry's exact stop and target distances are known.

## H4: same-entry counterfactual exits (diagnostic)

**Entry cohort** = every entry the frozen V1 logic actually made, per cohort: each of the six
strategies alone (independent session) and the live selector session, per symbol. Entry
time, side, strategy, fill price, volume, initial monetary risk, initial stop and target, and
entry-side costs are IDENTICAL across all exit variants. Each entry is replayed independently
from its fill bar to the end of its fold over the same bars, features and confirmed regime the
engine used, with the engine's own intrabar SL/TP rule (stop assumed first when both are in
range), next-bar-open fills for bar-close decisions, range-end close, and `_close_trade` cost
accounting. Protective stop and target are standing broker orders in every variant; no
variant can widen a stop, add size or average down.

| Variant | Exit rule after the entry |
|---|---|
| `V1` (reference, not a trial) | V1 review exactly: V1 thesis (entry re-trigger), full round-trip cost in the remaining-edge test, adaptive exit params (max hold 600 s, early TP 1.0 R, giveback, breakeven) |
| `ST` | stop/target only |
| `FH3`, `FH12`, `FH36` | stop/target + time exit after 3 / 12 / 36 M5 bars (15 min / 1 h / 3 h), decided at bar close, filled at next open |
| `THESIS` | V1 review with the H1-DIR holding thesis in place of the entry re-trigger |
| `MEV` | V1 review with the H1-DIR thesis AND the marginal future cost (H5 model) in the remaining-edge test |

**Replay fidelity check (must pass before any H4 number is used):** replaying `V1` must
reproduce the engine's own exits (time, reason, P/L) for the independent single-strategy
cohorts. The share reproduced exactly is reported; below 99 % the H4 run is FAILED.

Trials: 6 variants x 7 cohorts x 2 symbols = **84** (family `v2-H4:<symbol>`).

**H4 is diagnostic, not a tradeable result.** Replayed entries may overlap in time (the
independent V1 sessions were flat-only, so a longer hold would have skipped some later
entries). A variant that meets every development criterion below is therefore NOT a
candidate by itself: it must be confirmed by a full causal `run_backtest` with that exit as a
research hook (one additional trial, counted in the family) before it can be called a
development candidate.

Questions answered, per cohort, with paired statistics (the same entry under two exits):

- Q1 entry quality: mean gross R of `ST` and `FH*` and its 95 % interval. If every
  interval contains 0, there is no demonstrable gross entry edge at any tested horizon.
- Q2 exit effect: paired mean (variant gross R - V1 gross R) with its t statistic.
- Q3 cost share: mean cost R / |mean gross R|.

## H5: marginal future cost in hold-versus-close decisions

V1's remaining-edge test (`backtest.engine._review_open_trade`) charges the FULL round-trip
estimate (both spreads, round-trip commission, slippage, uncertainty) against the remaining
distance to target on every bar of an open position, although the entry half is already
paid. `research/v2/marginal_cost.py` defines

`marginal_future_cost = exit half-spread (current bar) + expected exit slippage + swap for
rollovers still ahead (0 at these horizons) + uncertainty margin on those`.

Exit commission is excluded: it is paid whether the position closes now or later, so it does
not change the hold-versus-close comparison (stated, and switchable in code for audit).

Variant `V1-MC` = V1 review exactly, with only the remaining-edge cost replaced by the
marginal cost. Trials: 1 x 7 cohorts x 2 symbols = **14** (family `v2-H5:<symbol>`). `MEV`
(H4) combines H5 with the H1-DIR thesis.

## Measurements (every trial)

The master prompt's section 35 list: trades, gross/cost/net P&L, gross/cost/net R, win and
loss rate, PF, average win/loss R, expectancy, max drawdown (cumulative R), max consecutive
wins/losses, mean/median holding time, MAE/MFE (R), peak R, realized R, giveback, exit-reason
shares (target, stop, timeout, thesis, regime reversal, early close, range end), profit
capture ratio; fold net R and positive folds; PSR; DSR within the family; PBO per family.
Segments: symbol, strategy, direction, UTC session, entry regime, volatility tercile, spread
tercile, confidence bucket, holding duration, UTC hour, weekday. Segment results are
descriptive: any segment-specific idea they suggest is POST-HOC and becomes a new
pre-registered hypothesis (e.g. H6-H9), never a filter applied to this data.

Stress (every trial): costs x1.1 / x1.2 / x1.3 / x1.5; spread charged at the symbol's
p75 / p90 / p95 bar spread; slippage x2. Stress rescales the cost components of the same
trades (stop/target touches are not re-simulated; stated as an approximation).

## Development criteria (unchanged; all required)

Net R > 0; mean gross R >= 1.5 x mean cost R; >= 8 of 12 folds net-positive; PSR(0) >= 0.95;
DSR >= 0.95 within the family; PBO <= 0.20; >= 100 trades; no risk-limit breach; still
net-positive at cost x1.2. H4 additionally needs the causal confirmation run above.

## Stopping rule

H4 and H5 are each run exactly once (tag `v2r2`). If no variant meets the criteria the
result is recorded as NO VALIDATED EDGE, all trials stay in the ledger, and the next step is
a new pre-registered hypothesis based on the measured root cause, not a re-tuning of these
variants. OOS stays sealed either way.
