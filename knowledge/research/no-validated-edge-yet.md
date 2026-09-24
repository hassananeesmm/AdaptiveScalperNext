---
type: Research Finding
id: research/no-validated-edge-yet
title: No strategy has a validated out-of-sample edge yet
description: As of this bundle version, no strategy has passed purged cross-validation, deflated-Sharpe and PBO checks on untouched out-of-sample data with verified costs.
tags: [research, validation, oos]
applies_to:
  symbols: [XAUUSD, GBPJPY, BTCUSD]
  strategies: [momentum_continuation, pullback_continuation, range_breakout, statistical_reversion, volatility_expansion, microstructure_acceleration]
version: 1
status: draft
stale_after: 2026-12-31T00:00:00Z
generated:
  by: claude-code/1
  at: 2026-09-24T00:00:00Z
sources:
  - id: research-layer
    resource: repo:adaptive_scalper/research/stats.py
    title: PSR / DSR / PBO implementation
  - id: oos-guard
    resource: repo:adaptive_scalper/backtest/oos.py
---
# Finding (unverified draft)

The research validation layer exists: purged K-fold, CPCV, the probabilistic Sharpe ratio,
the deflated Sharpe ratio, PBO via CSCV, and an append-only trial ledger. The OOS guard
refuses overlapping or reused ranges. No strategy has yet been run through the full
protocol on broker history with broker-confirmed costs. Until that happens:

- backtest and PAPER results are hypotheses, not evidence of edge;
- costs not measured on the DEMO account are UNVERIFIED_ASSUMPTION;
- nothing may be promoted on the strength of these results.

This concept stays `draft` until a human verifies it. The advisor lists it only under
`unverified`, never under `approved`.
