---
type: Strategy Definition
id: strategy/range_breakout
title: range_breakout (v1)
description: "Trade the decisive move that triggered a BREAKOUT regime classification (directive section 9, item 3)."
tags: [strategy, active]
strategy_key: range_breakout
strategy_version: 1
retired: false
applies_to:
  symbols: [XAUUSD, GBPJPY, BTCUSD]
  strategies: [range_breakout]
version: 1
status: stable
generated:
  by: claude-code/1
  at: 2026-09-24T00:00:00Z
sources:
  - id: code
    resource: repo:adaptive_scalper/strategies/range_breakout.py
    title: range_breakout implementation (authoritative)
---
# Definition

range_breakout: trade the decisive move that triggered a BREAKOUT
regime classification (directive section 9, item 3).

Direction follows the sign of the latest single-bar return — the same
signal the regime classifier itself used to distinguish BREAKOUT from
VOLATILITY_EXPANSION (decisive direction vs. none). Wider stop/target
than momentum_continuation since a breakout bar is, by definition, an
unusually wide one (directive section 13's BREAKOUT regime is only
reached via `range_expansion_ratio` above threshold).

# Regime scope

BREAKOUT

# Default parameters (code is authoritative)

| Parameter | Default |
|---|---|
| `stop_atr_multiple` | 2.0 |
| `target_atr_multiple` | 3.0 |
| `min_confidence` | 0.15 |
| `expected_duration_seconds` | 600 |

# Validation status

There is no validated out-of-sample edge yet. See
[the research status finding](/research/no-validated-edge-yet.md). This definition describes
what the strategy does. It is not evidence that the strategy is profitable, and it gives
the strategy no authority to trade.
