---
type: Architecture Decision
id: adr/single-execution-path
title: One guarded broker execution path
description: Only the three safe execution services call order_check/order_send, through one synchronized gateway, after a fresh final-permission evaluation.
tags: [architecture, execution, safety]
version: 1
status: stable
generated:
  by: claude-code/1
  at: 2026-09-24T00:00:00Z
sources:
  - id: architecture-audit
    resource: repo:tests/test_runtime_architecture.py
  - id: execution-service
    resource: repo:adaptive_scalper/execution/service.py
---
# Decision

- The MT5 gateway is constructed in exactly one place (`gateway/factory.py`), which always
  wraps it in `SynchronizedGateway`.
- `order_check`/`order_send` are called only by `execution/service.py` (entries),
  `execution/close.py` and `execution/stop_modification.py`.
- A DEMO entry goes through `submit_new_entry`. That builds fresh evidence twice and
  evaluates final permission before `order_check` and again before `order_send`.
- An `order_send` exception is an UNKNOWN outcome, not a failure. The order is quarantined
  and every new entry blocks until reconciliation proves what happened.

The AST audit in the test suite enforces all of this on every run.
