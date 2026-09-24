---
type: Engineering Decision
id: adr/causal-fill-model
title: Causal next-bar-open fill model (fill_model/v2)
description: Backtest and PAPER share one causal per-bar loop with next-bar-open fills, bid/ask triggers, entry-bar SL/TP and a single itemised cost charge.
tags: [backtest, paper, simulation, costs]
version: 2
status: stable
generated:
  by: claude-code/1
  at: 2026-09-24T00:00:00Z
sources:
  - id: engine
    resource: repo:adaptive_scalper/backtest/engine.py
  - id: regressions
    resource: repo:tests/test_backtest_correctness_regressions.py
---
# Decision

Each bar is processed in this order:

1. Fill decisions taken on the previous bar at this bar's open.
2. Compute features and regime.
3. Apply intrabar stop/target on bid/ask. Gap-through stops fill at the open. When both
   stop and target are touched, the stop is assumed to hit first.
4. Review open trades. Peak R only ever increases and persists across cycles.
5. Scan for new signals.

A deferred entry is revalidated at fill time (staleness, news, cost/edge, sizing, risk,
portfolio and correlation). Costs are charged once, with a per-component breakdown and a
provenance label. A cost that has not been measured is labelled UNVERIFIED_ASSUMPTION and
is never presented as broker-confirmed.
