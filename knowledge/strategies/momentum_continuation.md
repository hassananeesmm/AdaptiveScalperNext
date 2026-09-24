---
type: Strategy Definition
id: strategy/momentum_continuation
title: momentum_continuation (v1)
description: "Ride an already-confirmed trend (directive section 9's active strategy family list, item 1)."
tags: [strategy, active]
strategy_key: momentum_continuation
strategy_version: 1
retired: false
applies_to:
  symbols: [XAUUSD, GBPJPY, BTCUSD]
  strategies: [momentum_continuation]
version: 1
status: stable
generated:
  by: claude-code/1
  at: 2026-09-24T00:00:00Z
sources:
  - id: code
    resource: repo:adaptive_scalper/strategies/momentum_continuation.py
    title: momentum_continuation implementation (authoritative)
---
# Definition

momentum_continuation: ride an already-confirmed trend (directive
section 9's active strategy family list, item 1).

Fires only in a TRENDING_UP/TRENDING_DOWN regime; direction follows the
regime's direction. Confidence scales with regime confidence and the
feature engine's efficiency ratio (a clean, efficient trend is a
stronger hypothesis than a noisy one that happened to classify as
trending). Deliberately conservative: below `min_confidence`, returns
None (FLAT) rather than a weak signal — directive section 10, FLAT is a
valid decision, not a failure to find something.

# Regime scope

TRENDING_UP / TRENDING_DOWN

# Default parameters (code is authoritative)

| Parameter | Default |
|---|---|
| `stop_atr_multiple` | 1.5 |
| `target_atr_multiple` | 2.5 |
| `min_confidence` | 0.15 |
| `expected_duration_seconds` | 600 |

# Validation status

There is no validated out-of-sample edge yet. See
[the research status finding](/research/no-validated-edge-yet.md). This definition describes
what the strategy does. It is not evidence that the strategy is profitable, and it gives
the strategy no authority to trade.
