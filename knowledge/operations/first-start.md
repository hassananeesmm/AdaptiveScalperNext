---
type: Runbook
id: runbook/first-start
title: First start on the Windows laptop (PAPER, then DEMO)
description: Order of operations for a first run - setup, doctor, operator kill-switch bootstrap, PAPER burn-in, then DEMO.
tags: [runbook, windows, startup]
version: 2
status: stable
generated:
  by: claude-code/1
  at: 2026-09-24T12:00:00Z
sources:
  - id: handoff
    resource: repo:LOCAL_MT5_HANDOFF.md
---
# Steps

1. Run `SETUP.bat`. It creates `.venv`, installs pinned requirements and migrates the
   database.
2. Log the MT5 terminal into the **DEMO** account yourself. Credentials never go into the
   repository or config files.
3. Run `python -m adaptive_scalper.cli doctor`. Every check must pass, including account trade mode DEMO.
4. As the human operator, run `.venv\Scripts\python.exe -m adaptive_scalper.cli kill-switch bootstrap --operator-id <you> --reason "first start"`.
   Nothing does this automatically.
5. Run `START PAPER.bat` and burn in on real market data with simulated fills. Review
   `why-no-trade`, the dashboard and the PAPER results before going further.
6. Run one non-executing DEMO order check, `python -m adaptive_scalper.cli order-check-probe --symbol XAUUSD`,
   and record the retcode in BUG_BACKLOG item 5.
7. Only then run `START DEMO.bat`. Its banner must read: REAL-MONEY EXECUTION: DISABLED.
