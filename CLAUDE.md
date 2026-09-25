# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Scope boundary

Work only inside `C:\AdaptiveScalperNext`. Never modify, delete, rename,
overwrite, or run destructive operations against the sibling directory
`C:\AdaptiveScalper` (a separate, older project). Read-only inspection of it
for reference is fine; no writes, ever.

## Permanent project rules

1. `MASTER_BUILD_DIRECTIVE.md` is authoritative. Where anything else in this
   repository (including this file) conflicts with it, the directive wins.
2. At the beginning of every session, read `MASTER_BUILD_DIRECTIVE.md`,
   `PROJECT_STATUS.md`, and `WORKLOG.md`. If any of these do not yet exist,
   that reflects an early bootstrap state of the repository, not an error.
3. PAPER + MT5 DEMO only.
4. Never enable real-money trading.
5. Only `XAUUSD`, `GBPJPY`, and `BTCUSD` may be executable symbols.
6. `failed_breakout_fade` and `support_resistance_reaction` are permanently
   retired strategies — do not reintroduce or re-enable them.
7. Never weaken safety controls simply to create more trades.
8. Never delete failing tests just to make the suite green.
9. Never store secrets or broker credentials in Git.
10. Maintain `PROJECT_STATUS.md` and `WORKLOG.md` continuously as work
    happens, not retroactively.

## Current state

`MASTER_BUILD_DIRECTIVE.md`, `PROJECT_STATUS.md`, `WORKLOG.md` and
`BUG_BACKLOG.md` exist and are current; read them at session start. The
system is implemented end to end (PAPER + DEMO runtime, CLI, responsive
dashboard, research, learning observer, RAG + OKF knowledge) and verified on
the Windows laptop against the real IC Markets DEMO terminal (see
`docs/QA_REPORT.md`, Windows section). Pending: the operator's kill-switch
bootstrap, then DEMO execution (`LOCAL_MT5_HANDOFF.md` steps U-X). Broker
times are server clock converted to UTC in `Mt5Gateway` (`[mt5]
server_time_rule`). Architecture: `docs/ARCHITECTURE.md`; safety guarantees:
`docs/SAFETY.md`; dashboard: `docs/DASHBOARD.md`.

## Development environment

- Python 3.13 (`pyproject.toml`: `>=3.13,<3.14`).
- Virtual environment: `python -m venv .venv`, then
  `.venv\Scripts\activate`.
- Dependencies: `pip install -r requirements.txt`.
- Tests: `pytest` (config in `pytest.ini`, tests live under `tests/`).
- CLI: `python -m adaptive_scalper.cli --help`. `tests/test_mt5_gateway_live.py`
  needs a live MT5 terminal and skips elsewhere.
