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

This is a fresh project. `MASTER_BUILD_DIRECTIVE.md`, `PROJECT_STATUS.md`,
and `WORKLOG.md` have not been created yet. No trading system code has been
written. Only the development environment exists so far.

## Development environment

- Python 3.11+ (matches the target of the prior `AdaptiveScalper` project).
- Virtual environment: `python -m venv .venv`, then
  `.venv\Scripts\activate`.
- Dependencies: `pip install -r requirements.txt`.
- Tests: `pytest` (config in `pytest.ini`, tests live under `tests/`).

No trading logic, broker gateway, or strategy code exists yet — do not
assume any of the module boundaries from the old project apply here until
`MASTER_BUILD_DIRECTIVE.md` specifies the architecture.
