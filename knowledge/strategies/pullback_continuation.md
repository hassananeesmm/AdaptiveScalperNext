---
type: Strategy Definition
id: strategy/pullback_continuation
title: pullback_continuation (v1)
description: "Enter a confirmed trend on a short-term counter-move, rather than chasing the trend at its extreme (directive section 9, item 2)."
tags: [strategy, active]
strategy_key: pullback_continuation
strategy_version: 1
retired: false
applies_to:
  symbols: [XAUUSD, GBPJPY, BTCUSD]
  strategies: [pullback_continuation]
version: 1
status: stable
generated:
  by: claude-code/1
  at: 2026-09-24T00:00:00Z
sources:
  - id: code
    resource: repo:adaptive_scalper/strategies/pullback_continuation.py
    title: pullback_continuation implementation (authoritative)
---
# Definition

pullback_continuation: enter a confirmed trend on a short-term
counter-move, rather than chasing the trend at its extreme (directive
section 9, item 2).

Distinct from momentum_continuation (directive section 18: entry logic
concepts must stay distinct, not one identical score for everything).
Requires: trend regime AND the LATEST single bar moved against the
trend (`return_1` opposite sign) AND the longer `momentum` window still
confirms the trend direction — i.e. a genuine pullback within an intact
trend, not a reversal. Slightly discounted confidence vs. pure
continuation, since entering into an immediate counter-move.

# Regime scope

TRENDING_UP / TRENDING_DOWN

# Default parameters (code is authoritative)

| Parameter | Default |
|---|---|
| `stop_atr_multiple` | 1.2 |
| `target_atr_multiple` | 2.0 |
| `min_confidence` | 0.15 |
| `confidence_discount` | 0.8 |
| `expected_duration_seconds` | 480 |

# Validation status

There is no validated out-of-sample edge yet. See
[the research status finding](/research/no-validated-edge-yet.md). This definition describes
what the strategy does. It is not evidence that the strategy is profitable, and it gives
the strategy no authority to trade.
