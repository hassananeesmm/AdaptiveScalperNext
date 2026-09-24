---
type: Lesson Learned
id: lesson/entry-bar-stops-and-targets
title: Stops and targets can trigger on the entry bar itself
description: Ignoring the entry bar's own range overstated simulated results; stops and targets are now evaluated from the fill bar onward, stop first.
tags: [backtest, paper, simulation]
version: 1
status: draft
generated:
  by: claude-code/1
  at: 2026-09-24T00:00:00Z
sources:
  - id: regressions
    resource: repo:tests/test_backtest_correctness_regressions.py
---
# Lesson (unverified draft)

The original simulator only checked stops and targets from the bar after the fill. A
trade whose stop sat inside the fill bar's range survived in simulation when it would
have been stopped out live. The causal engine now evaluates the fill bar's remaining
range. When stop and target are both touched it assumes the stop hit first, and regression
tests pin this behaviour. Peak R is persisted across PAPER cycles for the same reason: an
exit rule that depends on peak R must not reset on restart.
