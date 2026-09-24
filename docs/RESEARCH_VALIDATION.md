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
