---
type: Safety Procedure
id: safety/risk-limits
title: Hard risk limits
description: The authoritative risk defaults and where they are enforced; knowledge, ML and RAG can never raise them.
tags: [safety, risk]
version: 1
status: stable
generated:
  by: claude-code/1
  at: 2026-09-24T00:00:00Z
sources:
  - id: config
    resource: repo:config/default.toml
  - id: governor
    resource: repo:adaptive_scalper/risk/governor.py
---
# Limits (from `config/default.toml`, enforced by the risk governor)

| Limit | Default |
|---|---|
| Risk per trade | 0.25% of equity |
| Total open + pending + proposed risk | 0.75% |
| Daily loss halt | 2% |
| Drawdown halt | 5% |
| Open positions | at most 2 in total, 1 per symbol |

The backtest and PAPER engines apply the same halts historically. OKF concepts must not
carry these values as frontmatter keys: the validator rejects control-like keys, because
knowledge is documentation and not configuration.
