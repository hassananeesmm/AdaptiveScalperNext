---
type: Lesson Learned
id: lesson/order-send-exception-is-unknown
title: An order_send exception is an UNKNOWN outcome, not a rejection
description: A timeout or exception from order_send may still have reached the broker, so the order must be quarantined and reconciled before any new entry.
tags: [execution, chaos, reconciliation]
version: 1
status: draft
generated:
  by: claude-code/1
  at: 2026-09-24T00:00:00Z
sources:
  - id: chaos-tests
    resource: repo:tests/test_broker_chaos.py
  - id: recovery
    resource: repo:adaptive_scalper/execution/recovery.py
---
# Lesson (unverified draft)

Deterministic chaos tests showed that treating an `order_send` exception as "nothing
happened" left orders in SUBMITTED with no entry block. The corrected behaviour:

- `order_check` raises: the entry is blocked and nothing is sent.
- `order_send` raises: the order moves to UNKNOWN, with an ORDER_UNKNOWN journal event and
  an UNKNOWN_OUTCOME incident. New entries stay blocked until reconciliation resolves it on
  positive broker proof.
- A process that dies mid-submission leaves SUBMITTED/ACCEPTED orders. Startup quarantines
  those to UNKNOWN before anything else runs.

The live-terminal behaviour still has to be confirmed on the Windows laptop's DEMO
terminal (see LOCAL_MT5_HANDOFF.md).
