# Research validation

The system treats every backtest and PAPER result as a **hypothesis**, never as evidence of
edge, until it has survived the protocol below on broker history with known costs. As of
this version, no strategy has. The OKF finding `research/no-validated-edge-yet` records this
and is deliberately left unverified.

## 1. Causal simulation (`backtest/engine.py`, `simulation/fill_model.py`, fill_model/v2)

Each bar is processed in this order:
1. Fill the previous bar's decisions at this bar's **open**.
2. Compute features and regime.
3. Apply intrabar stop and target on the bid/ask side:
   - gap-through stops fill at the open;
   - the fill bar's own range counts;
   - when both are touched, the stop is assumed first.
4. Review open trades. Peak R only rises and persists across PAPER cycles.
5. Scan for signals.

Costs:
- The spread, slippage, commission, swap and fee are each charged **once** and itemised.
- Every trade carries a cost provenance label: `UNVERIFIED_ASSUMPTION`,
  `EXPLICIT_TEST_FIXTURE`, `BROKER_SPEC_ESTIMATE` or `BROKER_DEMO_CONFIRMED`.
- The config fingerprint and the fill-model version are recorded as well.

Deferred entries are revalidated at fill time against:
- staleness and news;
- cost and edge;
- sizing and risk;
- portfolio and correlation.

The same historical risk halts apply as in DEMO (daily loss, drawdown).

## 2. Dataset usage ledger and untouched OOS (`backtest/dataset.py`, `backtest/oos.py`)

Every run records the datasets it used, and why.

Overlap is judged per **symbol**, whatever the resolution, provenance or checksum: an M1
range and an M5 range over the same period are the same market.

`oos` refuses a range that overlaps any TRAINING, VALIDATION or WALK_FORWARD_FOLD use, and
refuses a second OOS run. `--analysis-reuse` is an explicit, recorded **non-evidence**
re-analysis. The `backtest` command records its range as VALIDATION, so a range you have
looked at can never become OOS afterwards.

## 3. Fixed-configuration stability (`walk-forward`)

`run_walk_forward` evaluates **one fixed configuration** across sequential folds. Nothing is
re-fit between folds, and it is labelled `SEQUENTIAL_FIXED_CONFIG_EVALUATION`. It is a
stability check, not ML walk-forward.

## 4. Purged cross-validation and overfitting statistics (`research/`)

- **Purged K-fold and CPCV.** Each sample is a label interval (entry → exit). Training
  samples that overlap a test block are purged, and those starting within the embargo after
  a block are dropped.
- **Statistics:**
  - PSR (probabilistic Sharpe);
  - DSR (Sharpe deflated by the number of trials in the family, using the ledger's
    cross-trial Sharpe variance);
  - PBO via CSCV.
- **Trial ledger** (`research_trials`, append-only by trigger). Every backtest,
  sequential-fold run, purged-CV run and model walk-forward is recorded, **including failed
  ones**, so the trial count behind DSR is honest.

`purged-validation` applies purged K-fold, PSR and DSR to a recorded backtest run.

## 5. Path stress (`path-stress`)

`path-stress` replays the **same realized P/Ls in random orders**:
- The terminal equity is identical in every permutation, so it is reported as one number.
- Only path-dependent quantities vary: the drawdown distribution and the probability of ruin.

## 6. ML: model walk-forward and the training job (`learning/`)

- `model_walk_forward.py` uses expanding windows:
  1. Train **only on the past**.
  2. Purge label overlap, plus an optional gap.
  3. Validate on the next block.
- Out-of-fold outputs:
  - AUC, Brier and log loss;
  - Brier skill against the training base rate (it must be ≥ 1 % to count);
  - reliability bins and ECE;
  - subgroup stability by strategy, regime and session.
- `jobs.run_training_job`:
  1. Uses one origin per job (BACKTEST or PAPER, never pooled).
  2. Never reads OOS runs, and drops rows that overlap reserved OOS ranges.
  3. Runs the walk-forward, then the final temporal retrain.
  4. Registers the model as **BASELINE, never CURRENT**.
  5. Records a MODEL_WALK_FORWARD trial.
  6. Evaluates the promotion gate on the true facts and **reports** the result.
- Promotion requires the following. The job never sets "untouched OOS" and never promotes.
  - untouched OOS;
  - broker-confirmed costs;
  - calibration;
  - subgroup stability;
  - a rollback target;
  - human review.
- The Stage-1 observer scores proposals for the journal only. Stage 2 (bounded influence) is
  not implemented.

## 7. What counts as evidence

A strategy or model is "validated" only when all of the following hold:
- It passed purged CV **and** DSR **and** PBO across the family's recorded trials.
- It passed **one** untouched OOS run.
- It used `BROKER_DEMO_CONFIRMED` costs, gathered by `costs observed` from real DEMO fills.
- A human reviewed it. Record the approval as an OKF Research Finding verified by `human:<id>`.

## 8. Negative-result investigation (2026-09-26)

Source: the 15 recorded walk-forward folds (1,505 trades; BTCUSD 2025-10-01..2026-06-29,
GBPJPY and XAUUSD 2025-06-01..2026-06-30). This is a read-only query of existing research
records. No parameter was tuned. The reserved out-of-sample interval (2026-07-01..2026-09-18)
was not touched. Every fold stopped at the 5 % drawdown halt, so fold samples are truncated
by the risk rule, not by the data.

| Strategy | Trades | Gross | Costs | Net | Win % | Avg gross R | Avg cost R | Avg net R |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| microstructure_acceleration | 1,377 | +216.6 | 7,349.8 | −7,133.3 | 31.5 | +0.005 | 0.227 | −0.222 |
| statistical_reversion | 96 | +96.5 | 416.3 | −319.8 | 46.9 | +0.041 | 0.186 | −0.145 |
| momentum_continuation | 22 | +131.1 | 42.2 | +88.9 | 59.1 | +0.249 | 0.083 | +0.166 |
| range_breakout | 8 | −46.2 | 15.0 | −61.2 | 12.5 | −0.289 | 0.087 | −0.376 |
| volatility_expansion | 1 | +4.7 | 0.9 | +3.8 | — | — | — | — |
| pullback_continuation | 1 | −5.2 | 2.0 | −7.2 | — | — | — | — |

(Account-currency units of the simulation; gross is before simulated spread, slippage and
commission.)

Findings:

- **Concentration is confirmed, not assumed.** `microstructure_acceleration` produced 91.5 %
  of all walk-forward trades, with the same share on every symbol (BTCUSD 92.1 %, GBPJPY
  89.7 %, XAUUSD 93.1 %). 1,422 of the 1,505 trades were entered in `RANGE`.
- **Costs, not direction, explain the loss.** Its gross edge is essentially zero
  (+0.005 R/trade) while costs are 0.227 R/trade: costs are about 34× the gross result.
  It holds about 4.8 minutes on average, so frequent short trades pay spread and slippage
  with no edge to cover them. The live DEMO record agrees: 25 closed trades, −55.52 USD net,
  11W/14L, avg R −0.10 (docs/STRATEGY_LAB.md).
- **By exit:** stop-loss exits −8,207.5 and "thesis invalidated" exits −4,268.1 outweigh
  take-profit (+3,310.7) and max-hold (+1,036.9) exits.
- **By direction** both sides lose (BUY −3,943.8, SELL −3,484.9). By cost provenance both
  BROKER_DEMO_CONFIRMED (−4,938.1) and UNVERIFIED_ASSUMPTION (−2,490.6, GBPJPY) runs lose.
- **`momentum_continuation`** is the only strategy with a positive net result, but on 22
  trades. That is an insufficient sample and **not** evidence of an edge. It has never
  traded on DEMO.
- **Limitations:** no point-in-time historical news (the news gate is not reproduced in
  research); M5 bars with the evidence-based fill model; the drawdown halt truncates every fold.

No profitable strategy is claimed. Nothing here changes live configuration. Any change to
the active set or to parameters requires a separately reviewed change evaluated without the
reserved OOS interval, followed by the single untouched OOS run (section 7).
