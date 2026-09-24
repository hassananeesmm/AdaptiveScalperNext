# Knowledge log

- 2026-09-24: bundle created (OKF v0.2). Architecture decisions, the six active strategy
  definitions plus the two retired ones, safety procedures, runbooks, the entry-observer
  model card, and two lessons learned from PAPER/backtest and broker-chaos work. Evidence
  concepts start as `draft` until a human verifies them.
- 2026-09-24: runbooks v2 -- corrected the kill-switch bootstrap flag (`--operator-id`, `--reason`) and
  use the `python -m adaptive_scalper.cli` invocation; the first-start runbook now includes the
  non-executing `order-check-probe` step.
