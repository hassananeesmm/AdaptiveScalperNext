"""RESEARCH-ONLY Strategy V2 candidates (pre-registered in
docs/research/V2_PREREGISTRATION_2026-09-29.md).

Nothing in this package can reach `order_send`:

- it is imported by no runtime, PAPER, execution, selector or strategy
  module (`tests/test_research_v2_boundary.py`);
- it only runs through `backtest.engine.run_backtest(research_selector=...,
  research_holding_thesis=...)`, which refuses those hooks for any origin
  other than BACKTEST and for incremental (PAPER-style) calls;
- the six V1 strategies and the live selector are frozen
  (`tests/test_v1_strategy_freeze.py`); V2 wraps their signals, it never
  edits them.

A V2 candidate may affect DEMO only after the full promotion gate
(development criteria, one untouched OOS run, broker-confirmed costs,
human review, versioned release, PAPER burn-in). Never auto-promoted.
"""
