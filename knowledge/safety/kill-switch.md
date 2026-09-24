---
type: Safety Procedure
id: safety/kill-switch
title: Kill switch policy
description: The kill switch fails closed, never clears itself, and can only be bootstrapped or cleared by an explicit human operator command.
tags: [safety, kill-switch, operator]
version: 1
status: stable
generated:
  by: claude-code/1
  at: 2026-09-24T00:00:00Z
sources:
  - id: kill-switch
    resource: repo:adaptive_scalper/core/kill_switch.py
  - id: architecture-audit
    resource: repo:tests/test_runtime_architecture.py
---
# Policy

- The states are UNINITIALIZED, INVALID, ENGAGED and DISENGAGED. Only DISENGAGED permits
  new exposure. A new database starts UNINITIALIZED, so it cannot trade until an operator
  bootstraps it.
- Any component may **engage** the switch, because that only makes the system safer.
- **Bootstrap** and **clear** require an `OperatorAuthority`. Only the operator CLI
  constructs one. They are never called from startup, the runtime, launchers, ML, RAG or
  OKF, and the architecture audit enforces this.
- Every transition is atomic with its audit row in `configuration_audit`.
- An engaged switch blocks new entries only. Open positions keep being managed:
  protective stops, reviews and reconciliation continue.
