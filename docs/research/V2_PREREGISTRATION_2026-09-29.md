# Strategy V2 research pre-registration (2026-09-29)

**Status: PRE-REGISTERED before any V2 run.** Research-only. Nothing here can reach
`order_send`: V2 code lives in `adaptive_scalper/research/v2/`, is imported by no runtime,
PAPER, execution or selector module (enforced by `tests/test_research_v2_boundary.py`), and
runs only through `run_backtest(..., origin=BACKTEST)` with research hooks that refuse any
other origin.

## Data and protocol (fixed)

- Research database: the existing read-only snapshot copy
  `data/research/snapshot_20260927T080325Z.sqlite3` (never production).
- Symbols: BTCUSD 2025-10-01..2026-06-30, XAUUSD 2025-06-01..2026-06-30 (identical to the V1
  independent study). GBPJPY excluded (costs `UNVERIFIED_ASSUMPTION`).
- 12 sequential fixed-configuration folds, each starting flat at 10,000; unchanged live risk
  limits (0.25 / 0.75 / 2 / 5 %). Costs: the configured `BROKER_DEMO_CONFIRMED` fill assumptions.
- **Protected OOS 2026-07-01..2026-09-18 is not read** (`assert_outside_reserved_oos`).
- Every trial below is recorded in the research DB trial ledger, including failures and
  rejections. No trial is added after results are seen without being labelled
  `POST-HOC` in the report and counted in the DSR family.
- Candidate calibration (H3) is fitted on EARLIER folds only (expanding window, fold k uses
  folds < k); fold 1 has no calibration and therefore abstains.

## Hypotheses and the complete trial grid

### H1 — entry signal vs holding thesis (all six strategies, V1 entries unchanged)

V1 re-evaluates an open position by re-running the strategy's *entry* trigger; if it no longer
fires in the same direction the position is closed ("original strategy thesis invalidated").

- **H1-DIR** holding thesis = *directional invalidation only*: the position stays valid unless
  the same strategy now fires in the OPPOSITE direction, or the confirmed regime is a trend
  opposite to the position (TRENDING_UP vs SELL, TRENDING_DOWN vs BUY).
- **H1-NONE** (control) no setup re-trigger test at all.

V1's "thesis invalidated" has a second cause that stays in EVERY variant: the remaining edge to the
target, net of the current cost estimate, below the minimum (expected remaining reward,
`position_management.expectancy`). H1 replaces only the entry re-trigger half. (Clarified 2026-09-29
before any V2 run, after a harness test showed the second cause.)

Neither can loosen the broker stop, add risk or average down (the thesis only decides *whether
to close early*). Trials: 2 variants × 6 strategies × 2 symbols = **24**.

### H2 — friction filter on entries (V1 exits unchanged)

Accept a signal only if `target_distance >= K × estimated round-trip cost` at the signal bar,
plus a cooldown of 6 bars after any exit for the two high-frequency strategies
(`microstructure_acceleration`, `statistical_reversion`).
K ∈ {3, 5} (fixed before running). Trials: 2 × 6 × 2 = **24**.

### H3 — calibrated selector V2 (all six V1 strategies as candidates)

- Keep `raw_confidence` for observability; never use it as a probability.
- Per strategy × symbol, from earlier folds only: reliability bins of raw_confidence
  (quintile edges from the calibration sample) → out-of-fold mean GROSS R, its standard error
  and win rate. Minimum 30 trades per bin, otherwise the strategy-level rate; fewer than 50
  trades per strategy → **UNKNOWN → abstain** (no default probability).
- Expected cost R = measured mean cost R of that strategy × symbol from earlier folds.
- Select the candidate with the highest `(mean gross R − 1 SE) − M × cost R`; **abstain (FLAT)**
  unless it is > 0. Margin M ∈ {1.0, 1.5}. Trials: 2 × 2 symbols = **4**.

**Total pre-registered trials: 52** (DSR family sizes: H1 24, H2 24, H3 4; plus the 21 V1 trials).

## Measurements (every trial)

Trades, win %, gross R, cost R, net R, PF, max fold drawdown, halted folds, PSR, DSR within the
family, exit-reason table, holding-duration buckets (<5, 5–15, 15–60, ≥60 min), MAE/MFE in R
(computed from the bars between entry and exit), strategy selection distribution (H3).
PBO across each family's variants.

## Development promotion criteria (all required; unchanged policy)

1. Net R > 0 on development data after realistic costs, AND mean gross R ≥ 1.5 × mean cost R.
2. ≥ 8 of 12 folds net-positive; PSR(0) ≥ 0.95; DSR ≥ 0.95 within its family; PBO ≤ 0.2.
3. ≥ 100 trades.
Meeting these would ONLY permit locking one candidate for the single untouched OOS run, which
requires explicit human approval. No DEMO or PAPER configuration changes from this study.
