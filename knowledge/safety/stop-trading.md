---
type: Safety Procedure
id: safety/stop-trading
title: STOP TRADING engages the kill switch; it does not flatten positions
description: The emergency stop engages the kill switch so no new exposure is taken; it deliberately does not blindly close open positions.
tags: [safety, kill-switch, operations]
version: 1
status: stable
generated:
  by: claude-code/1
  at: 2026-09-24T00:00:00Z
sources:
  - id: kill-switch
    resource: repo:adaptive_scalper/core/kill_switch.py
---
# Procedure

1. Run `STOP TRADING.bat`, or `adaptive-scalper kill-switch engage --reason "<why>"`.
2. The kill switch becomes ENGAGED. New entries are blocked immediately, including an
   entry that is in flight, which fails its second final-permission check.
3. Open positions keep their broker-side protective stops, and the position cycle keeps
   managing them.
4. To flatten a position, close it deliberately (per position) after reviewing it. A
   blanket close can realise losses in thin markets and is never automatic.
5. Resume only by an explicit operator `kill-switch clear` once the cause is understood.
