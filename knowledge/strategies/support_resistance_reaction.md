---
type: Strategy Definition
id: strategy/support_resistance_reaction
title: support_resistance_reaction (PERMANENTLY RETIRED)
description: A permanently retired strategy family. It is documented only so its retirement is never mistaken for an omission.
tags: [strategy, retired]
strategy_key: support_resistance_reaction
retired: true
version: 1
status: deprecated
generated:
  by: claude-code/1
  at: 2026-09-24T00:00:00Z
sources:
  - id: constants
    resource: repo:adaptive_scalper/config/constants.py
    title: RETIRED_STRATEGY_KEYS (the single hard-coded source of truth)
  - id: directive
    resource: repo:MASTER_BUILD_DIRECTIVE.md
    title: Master build directive, section 8
---
# Status: permanently retired

`support_resistance_reaction` is listed in `RETIRED_STRATEGY_KEYS`. It must never register, signal, propose,
rank, train, promote or execute. That holds through restarts, config reloads, migrations,
model-registry restores, RAG, OKF and packaging. `StrategyRegistry.register()` refuses it,
and engine startup refuses to run if it is active.

This concept is `deprecated` and `retired: true`. The knowledge validator rejects any
concept that scopes itself to a retired strategy without both of those markers. Knowledge
can record a retirement but can never undo one.
