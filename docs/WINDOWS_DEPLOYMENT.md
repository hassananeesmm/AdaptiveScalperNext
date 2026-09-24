# Windows deployment

The target is one Windows laptop that hosts the MetaTrader 5 terminal. MT5's Python
integration only works on that machine. **Never** deploy this as a cloud trading service.

## Requirements

- Windows 10 or 11 with MetaTrader 5 installed, logged into a **DEMO** account.
- Python **3.13**, available as `py -3.13`.
- The repository at `C:\AdaptiveScalperNext`.
- Everything runs as the logged-in user. No service account or admin rights are needed.

## Install

1. Double-click `SETUP.bat`, or run the steps in `LOCAL_MT5_HANDOFF.md` B–D. It:
   - creates `.venv`;
   - installs the pinned requirements (including `MetaTrader5==5.0.6180`, which is
     Windows-only);
   - runs `doctor`, which creates and migrates `data\adaptive_scalper.sqlite3`.
2. Run `.venv\Scripts\python.exe -m adaptive_scalper.cli symbols`. It resolves the three
   symbols and stores their broker specs for offline research.
3. Run `powershell -ExecutionPolicy Bypass -File scripts\windows_verify.ps1`.

## Configuration

- `config/default.toml` is Git-tracked and non-secret.
- `[risk]`: the hard defaults. Do not raise them.
- `[runtime]`: cadences, log directory, `paper_session_tag`, `magic`.
- `[costs.SYMBOL]`: commission, slippage and swap evidence, plus its provenance label.
  Unknown values stay unset, which blocks DEMO entries rather than assuming zero.
- **Credentials never go in any file.** The MT5 terminal holds the login.

## Operating

| Action | How |
|---|---|
| Start PAPER | `START PAPER.bat` |
| Start DEMO (after the handoff steps) | `START DEMO.bat` |
| Watch | `START DASHBOARD.bat` → http://127.0.0.1:8765/ |
| Why is nothing trading? | `python -m adaptive_scalper.cli why-no-trade` |
| Stop new entries now | `STOP TRADING.bat`. Positions are **not** closed. |
| Resume | `kill-switch clear --operator-id YOU --reason "..."` (explicit operator decision) |
| Stop the runtime | Ctrl+C in its window. Stopping never closes positions; broker-side stops remain. |

Logs are written to `logs\` (a JSONL machine log plus a rotating human log, with secrets
redacted). The database is `data\adaptive_scalper.sqlite3`. `logs\` and `data\` are Git-ignored.

## Launcher checks (manual, once)

The cloud checked the launchers statically: every command parses against the real CLI, the
banner text is exact, and the kill-switch rules hold. On the laptop, confirm each one:

- `SETUP.bat` finishes with the doctor output and the bootstrap hint. It must **not** change
  the kill switch.
- `START PAPER.bat` prints the PAPER banner and the startup JSON, and Ctrl+C exits cleanly.
- `START DEMO.bat` prints the three-line banner. With the terminal on a REAL account it must
  refuse to start.
- `START DASHBOARD.bat` opens the browser, and the header shows "REAL-MONEY EXECUTION:
  DISABLED".
- `RUN BACKTEST.bat` prompts for a symbol and dates, and prints the JSON result.
- `STOP TRADING.bat` leaves `kill-switch status` at ENGAGED, and open positions are
  untouched.

## Backups

The database is a single SQLite file in WAL mode. To back it up:
1. Stop the runtime.
2. Copy `data\adaptive_scalper.sqlite3` (and any `-wal` and `-shm` files).
3. Start the runtime again.

Never edit the database by hand to clear a block.
