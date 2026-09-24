---
type: Model Card
id: model/entry-observer
title: Entry observer (Stage 1, zero influence)
description: The ML entry observer scores proposals for the journal only. It has no influence on selection, sizing, permission or execution.
tags: [ml, model-card, observer]
version: 1
status: draft
generated:
  by: claude-code/1
  at: 2026-09-24T00:00:00Z
sources:
  - id: observer
    resource: repo:adaptive_scalper/learning/observer.py
---
# Model card (draft)

- **Stage:** 1 (observer). `influence` is always `NONE`, and there is no Stage 2 path in code.
- **Outputs:** `SCORED`, `NO_MODEL`, `NOT_SCORABLE` or `DEGRADED`, journaled as MODEL_USED
  evidence.
- **Training data:** none validated yet. With no trained model in the registry, the
  observer reports `NO_MODEL`.
- **Promotion:** training never implies promotion. Any future promotion needs
  out-of-sample evidence recorded in the trial ledger and explicit human review.
- **Failure mode:** an exception degrades advisory health. It never blocks or allows a
  trade.
