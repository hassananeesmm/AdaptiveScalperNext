"""Causal backtest / walk-forward / untouched-OOS / Monte Carlo research
infrastructure (directive section 80).

- `engine.run_backtest()` -- single-shot causal backtest over one
  symbol/resolution's bar history, using the SAME production decision
  cores (strategies/regime/expectancy/adaptive-exit/risk-governor) the
  live system uses.
- `walk_forward.run_walk_forward()` -- N sequential, non-overlapping (
  optionally embargoed) folds of `run_backtest()`, walked forward in time.
- `oos.run_untouched_oos()` -- the only sanctioned way to run a genuine
  out-of-sample validation; fails closed if the exact dataset was ever
  previously used for training/validation/a walk-forward fold, or
  already spent as OOS once before.
- `monte_carlo.run_monte_carlo()` -- trade-order resampling over a
  completed run's REALIZED P/L sequence (never fabricated outcomes).
- `dataset.py` -- content-checksummed dataset snapshots and the
  dataset-usage ledger `oos.py` consults (directive section 65).
- `persistence.record_backtest_run()` -- durable SQLite recording of a
  run's dataset, dataset-usage, and trades (migration `0014_backtest`).
"""

from __future__ import annotations
