---
type: Strategy Definition
id: strategy/microstructure_acceleration
title: microstructure_acceleration (v1)
description: "Very-short-horizon signal on aligned, meaningful velocity + acceleration (directive section 9, item 6)."
tags: [strategy, active]
strategy_key: microstructure_acceleration
strategy_version: 1
retired: false
applies_to:
  symbols: [XAUUSD, GBPJPY, BTCUSD]
  strategies: [microstructure_acceleration]
version: 1
status: stable
generated:
  by: claude-code/1
  at: 2026-09-24T00:00:00Z
sources:
  - id: code
    resource: repo:adaptive_scalper/strategies/microstructure_acceleration.py
    title: microstructure_acceleration implementation (authoritative)
---
# Definition

microstructure_acceleration: very-short-horizon signal on aligned,
meaningful velocity + acceleration (directive section 9, item 6).

Unlike the other five, this one is not regime-restricted to a single
state — short-horizon acceleration bursts can occur inside a trend,
range, or breakout alike — but it deliberately excludes COMPRESSION,
ERRATIC, and UNKNOWN, where a short-horizon acceleration reading is
either meaningless (compression: everything is small by definition) or
untrustworthy (erratic/unknown). Fires only when velocity and
acceleration agree in sign AND the acceleration is large relative to
ATR — a small, noisy wiggle should not trigger a signal.

# Regime scope

any except COMPRESSION, ERRATIC, UNKNOWN

# Default parameters (code is authoritative)

| Parameter | Default |
|---|---|
| `min_acceleration_to_atr_ratio` | 0.05 |
| `stop_atr_multiple` | 1.0 |
| `target_atr_multiple` | 1.5 |
| `min_confidence` | 0.15 |
| `expected_duration_seconds` | 180 |

# Validation status

There is no validated out-of-sample edge yet. See
[the research status finding](/research/no-validated-edge-yet.md). This definition describes
what the strategy does. It is not evidence that the strategy is profitable, and it gives
the strategy no authority to trade.
