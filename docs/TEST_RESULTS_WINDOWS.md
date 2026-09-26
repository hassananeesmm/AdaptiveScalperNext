# Windows Test Results

Date: 2026-09-25. Interpreter: `C:\AdaptiveScalperNext\.venv\Scripts\python.exe`
(Python 3.13.15).

## Results

| Check | Exact command | Result |
|---|---|---|
| **2026-09-26 full suite (0.2.0 branch)** | `.venv\Scripts\python.exe -m pytest -q` | **1557 passed, 9 skipped**, 2 third-party warnings, 246.09 s |
| 2026-09-26 Strategy Lab tests | `pytest -q tests\test_strategy_lab_attribution.py tests\test_dashboard*.py` | 65 passed |
| 2026-09-26 dependency / security | `pip check`; temporary `pip-audit -r requirements.txt`; `bandit -r adaptive_scalper` | clean; no known vulnerabilities; no new findings |
| 2026-09-26 browser (viewport emulation) | Chromium via Playwright, 1366x768, 1600x900, 1920x1080, 2560x1440, 1536x864, 1280x720, 1093x614, 911x512 | no page overflow, 0 chart label overlaps, 0 JS errors; not native Windows DPI |
| Full suite (final) | `.venv\Scripts\python.exe -m pytest -q` | **1521 passed, 9 skipped**, 2 third-party deprecation warnings, 113.35 s |
| Release build gate (0.1.3) | `scripts\build_release.ps1 -Version 0.1.3` | **1521 passed, 9 skipped**, 110.78 s; archive built |
| Installed release smoke (0.1.3) | `scripts\release_smoke_test.ps1 -Zip dist\AdaptiveScalperNext-0.1.3.zip` | **PASS**; packaged suite 1521 passed, 9 skipped, 117.46 s; MT5 disabled |
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

- A separate operator session stopped PAPER, disengaged the kill switch and started DEMO.
- DEMO runtime: RUNNING with current broker data and task failure counts zero.
- Kill switch: DISENGAGED by that operator session; the audit did not mutate it.
- Broker: connected DEMO; terminal/account expert permissions true.
- Broker orders/positions at final read-only snapshot: zero. One stale reconciliation
  incident remained from an earlier orphan pending order; ASN-012 fixes its lifecycle in
  source, pending controlled runtime restart.
- Database: 275 MB main file plus active WAL; online backup used instead of copying only the
  main file.

## Tests deliberately not claimed

- The audit did not call `order_send`; naturally occurring DEMO trades did occur after the
  separate operator session started DEMO.
- No open-position DEMO restart or protective-stop lifecycle.
- No actual Windows display-setting changes at 125%/150%; scaling-equivalent Chrome viewports
  were used.
- No destructive disk-full or database-corruption injection against the real database.
