# Windows Test Results

Date: 2026-09-25. Interpreter: `C:\AdaptiveScalperNext\.venv\Scripts\python.exe`
(Python 3.13.15).

## Results

| Check | Exact command | Result |
|---|---|---|
| Full suite (final) | `.venv\Scripts\python.exe -m pytest -q` | **1517 passed, 9 skipped**, 2 third-party deprecation warnings, 119.54 s |
| Preflight regression | `.venv\Scripts\python.exe -m pytest -q tests\test_preflight.py tests\test_cli_commands.py::test_every_directive_command_is_registered` | **4 passed**, 8.10 s |
| Dependency consistency | `.venv\Scripts\python.exe -m pip check` | No broken requirements |
| Vulnerability audit | temporary `pip-audit -r requirements.txt` | No known vulnerabilities found |
| Database/source backup | SQLite online backup + `PRAGMA integrity_check` | source=ok, backup=ok |
| Live doctor | `.venv\Scripts\python.exe -m adaptive_scalper.cli doctor` | schema 28, UTC rows, DEMO, all clocks VERIFIED, doctor OK |
| Live preflight | `.venv\Scripts\python.exe -m adaptive_scalper.cli preflight` | `READY_FOR_PAPER`; three DEMO blockers |
| Byte compile | `.venv\Scripts\python.exe -m compileall -q adaptive_scalper` | recorded at final checkpoint |

An earlier pre-change run was 1514 passed, 9 skipped in 260.97 s. The nine skips are eight opt-in live MT5 tests and one symlink-privilege test. Live MT5 tests
were run in the preceding Windows-validation checkpoint and recorded there; the broad audit
suite intentionally blocks MT5 imports to ensure tests cannot touch the terminal.

No `ruff`, `bandit` or project-installed `pip-audit` executable was present. Ruff/Bandit were
not silently added as dependencies. Pip-audit was installed into an isolated temporary
directory and did not modify project requirements or the project virtual environment.

## Live state observed

- PAPER runtime: RUNNING, current broker data, task failure counts zero.
- Kill switch: UNINITIALIZED, blocking all simulated and broker entries.
- Broker: connected DEMO; terminal/account expert permissions true.
- Orders/positions: no dangerous UNKNOWN and no open execution incidents.
- Database: 275 MB main file plus active WAL; online backup used instead of copying only the
  main file.

## Tests deliberately not claimed

- No `order_send` test and no naturally occurring DEMO trade.
- No open-position DEMO restart or protective-stop lifecycle.
- No actual Windows display-setting changes at 125%/150%; scaling-equivalent Chrome viewports
  were used.
- No destructive disk-full or database-corruption injection against the real database.
