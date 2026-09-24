"""Causal backtest / sequential-fold / untouched-OOS / path-stress research
infrastructure (directive section 80).

- `engine.run_backtest()` -- single-shot causal backtest over one
  symbol/resolution's bar history, using the SAME production decision
  cores (strategies/regime/expectancy/adaptive-exit/risk-governor) the
  live system uses.
- `walk_forward.run_walk_forward()` -- N sequential, non-overlapping (
  optionally embargoed) folds of `run_backtest()` over ONE fixed
  configuration (SEQUENTIAL_FIXED_CONFIG_EVALUATION: nothing is re-fit
  between folds, so this is a stability check, not ML walk-forward --
  see `learning/model_walk_forward.py` for train/purge/validate/retrain).
- `oos.run_untouched_oos()` -- the only sanctioned way to run a genuine
  out-of-sample validation; fails closed if ANY time-overlapping range
  of the same symbol was ever used for training/validation/a fold, or
  already spent as OOS once before.
- `path_stress.run_trade_order_path_stress()` -- TRADE_ORDER_PATH_STRESS:
  permutations of a completed run's REALIZED P/L sequence (never
  fabricated outcomes). Drawdown/ruin only; terminal equity is invariant.
- `dataset.py` -- content-checksummed dataset snapshots and the
  dataset-usage ledger `oos.py` consults (directive section 65).
- `persistence.record_backtest_run()` -- durable SQLite recording of a
  run's dataset, dataset-usage, and trades (migration `0014_backtest`).
"""

from __future__ import annotations
