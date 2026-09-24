---
type: Strategy Definition
id: strategy/statistical_reversion
title: statistical_reversion (v1)
description: "Fade an extreme within a RANGE/COMPRESSION regime, expecting reversion toward the middle of the recent range (directive section 9, item 4)."
tags: [strategy, active]
strategy_key: statistical_reversion
strategy_version: 1
retired: false
applies_to:
  symbols: [XAUUSD, GBPJPY, BTCUSD]
  strategies: [statistical_reversion]
version: 1
status: stable
generated:
  by: claude-code/1
  at: 2026-09-24T00:00:00Z
sources:
  - id: code
    resource: repo:adaptive_scalper/strategies/statistical_reversion.py
    title: statistical_reversion implementation (authoritative)
---
# Definition

statistical_reversion: fade an extreme within a RANGE/COMPRESSION
regime, expecting reversion toward the middle of the recent range
(directive section 9, item 4).

Uses `recent_high`/`recent_low` from the feature engine's lookback
window to locate price within its recent range; a close within
`proximity_threshold` of either extreme is treated as an overextension
worth fading. Only fires in RANGE/COMPRESSION — a reversion bet inside a
TRENDING or BREAKOUT regime is exactly the kind of mistake this
distinction exists to prevent (directive section 13's whole point is
that different regimes call for different logic).

# Regime scope

RANGE / COMPRESSION

# Default parameters (code is authoritative)

| Parameter | Default |
|---|---|
| `proximity_threshold` | 0.15 |
| `stop_atr_multiple` | 1.0 |
| `target_atr_multiple` | 1.5 |
| `min_confidence` | 0.15 |
| `expected_duration_seconds` | 300 |

# Validation status

There is no validated out-of-sample edge yet. See
[the research status finding](/research/no-validated-edge-yet.md). This definition describes
what the strategy does. It is not evidence that the strategy is profitable, and it gives
the strategy no authority to trade.
