---
type: Strategy Definition
id: strategy/volatility_expansion
title: volatility_expansion (v1)
description: "Trade wick-rejection direction during a VOLATILITY_EXPANSION regime — a wide bar without the decisive directional persistence that would instead classify it as BREAKOUT (directive section 9, item 5; section 13 distinguishes the two regimes on exactly that basis)."
tags: [strategy, active]
strategy_key: volatility_expansion
strategy_version: 1
retired: false
applies_to:
  symbols: [XAUUSD, GBPJPY, BTCUSD]
  strategies: [volatility_expansion]
version: 1
status: stable
generated:
  by: claude-code/1
  at: 2026-09-24T00:00:00Z
sources:
  - id: code
    resource: repo:adaptive_scalper/strategies/volatility_expansion.py
    title: volatility_expansion implementation (authoritative)
---
# Definition

volatility_expansion: trade wick-rejection direction during a
VOLATILITY_EXPANSION regime — a wide bar without the decisive
directional persistence that would instead classify it as BREAKOUT
(directive section 9, item 5; section 13 distinguishes the two regimes
on exactly that basis).

Direction comes from candle wick asymmetry: a long lower wick (price
rejected from the bar's low) suggests upward continuation; a long upper
wick suggests downward continuation. This is standard causal price-action
reasoning — both wicks are fully determined by the CURRENT bar's own
OHLC, no future information involved.

# Regime scope

VOLATILITY_EXPANSION

# Default parameters (code is authoritative)

| Parameter | Default |
|---|---|
| `wick_asymmetry_threshold` | 0.15 |
| `stop_atr_multiple` | 2.0 |
| `target_atr_multiple` | 2.5 |
| `min_confidence` | 0.15 |
| `expected_duration_seconds` | 420 |

# Validation status

There is no validated out-of-sample edge yet. See
[the research status finding](/research/no-validated-edge-yet.md). This definition describes
what the strategy does. It is not evidence that the strategy is profitable, and it gives
the strategy no authority to trade.
