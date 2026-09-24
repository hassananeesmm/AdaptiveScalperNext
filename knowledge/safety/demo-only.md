---
type: Safety Procedure
id: safety/demo-only
title: PAPER and MT5 DEMO only; real-money execution is disabled
description: The system has exactly two modes, PAPER and DEMO, and refuses to start against any account whose trade mode is not DEMO.
tags: [safety, mode, account]
version: 1
status: stable
generated:
  by: claude-code/1
  at: 2026-09-24T00:00:00Z
sources:
  - id: constants
    resource: repo:adaptive_scalper/config/constants.py
  - id: engine
    resource: repo:adaptive_scalper/runtime/engine.py
---
# Procedure

- `ALLOWED_MODES` is exactly {PAPER, DEMO}. No LIVE or REAL mode exists, and an AST audit
  fails the test suite if one appears.
- Startup refuses REAL and CONTEST accounts in both modes, including PAPER. The account's
  trade mode is re-checked before every broker mutation.
- Only XAUUSD, GBPJPY and BTCUSD are executable. A symbol that cannot be resolved is
  excluded and never substituted.
- Never add a real-money mode, and never weaken DEMO verification to get more trades.
