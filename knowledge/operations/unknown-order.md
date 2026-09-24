---
type: Runbook
id: runbook/unknown-order
title: Handling an UNKNOWN order outcome
description: What to do when an order is UNKNOWN or PENDING_RECONCILIATION and new entries are blocked.
tags: [runbook, execution, reconciliation]
version: 1
status: stable
generated:
  by: claude-code/1
  at: 2026-09-24T00:00:00Z
sources:
  - id: recovery
    resource: repo:adaptive_scalper/execution/recovery.py
  - id: lesson
    resource: /lessons/order-send-exception-is-unknown.md
---
# Steps

1. Run `adaptive-scalper status`. The dangerous-UNKNOWN block names the order.
2. Run `adaptive-scalper reconcile`. This is read-only against the broker plus local
   repair. Resolution needs positive broker proof: a matching IN deal, or a terminal
   history state.
3. If the order stays PENDING_RECONCILIATION, compare it in the MT5 terminal (History tab)
   by the request token in the order comment. Do not guess.
4. Never hand-edit order state in SQLite, and never clear the kill switch to get around
   the block. The block is the safety feature.
