# Local MT5 handoff: Windows laptop

The cloud built and tested everything that can run without a broker terminal. This document
covers everything that cannot: it is the step-by-step sequence to run on the Windows laptop
that hosts the MetaTrader 5 terminal.

> **REAL-MONEY EXECUTION IS DISABLED.** The runtime refuses any account whose trade mode is
> not DEMO, in PAPER as well as DEMO. There is no LIVE mode.
>
> **Nothing in this sequence may be skipped to get a trade.** Zero DEMO trades is a valid
> outcome.

> **Laptop status (2026-09-25):** steps A-O and the PAPER part of P-Q are done and recorded
> in `docs/QA_REPORT.md` (Windows section) and `WORKLOG.md` W1-W7: environment, schema 28,
> server-clock conversion, full + live tests, doctor/symbols/reconcile, `order_check`
> (retcode 0), real backtests / walk-forward / path stress / purged validation (OOS
> 2026-07-01..2026-09-18 reserved, never run), live PAPER + dashboard. **Pending operator
> actions:** step P's kill-switch bootstrap (until then PAPER blocks every simulated entry),
> then steps U-X (start DEMO, first natural order). Use `START PAPER + DASHBOARD.bat` /
> `START DEMO + DASHBOARD.bat` for the combined start.

- **Project root:** `C:\AdaptiveScalperNext`
- **Canonical Python:** `C:\AdaptiveScalperNext\.venv\Scripts\python.exe`, written below as
  `%PY%`

```bat
cd /d C:\AdaptiveScalperNext
set PY=C:\AdaptiveScalperNext\.venv\Scripts\python.exe
```

Keep a copy of every command's output. The verification report is written to
`logs\windows_verify_*.txt`. Nothing you record should contain your login, password or
server credentials. The CLI masks the login number.

---

## A. Fetch the reviewed code

Use the reviewed PR branch (`claude/pensive-newton-tckoid`), or `main` after you have merged
the PR yourself:

```bat
git fetch origin
git checkout claude/pensive-newton-tckoid
git pull origin claude/pensive-newton-tckoid
git log --oneline -1
```

Check that the commit matches the PR's head SHA.

## B. Create or update `.venv`

```bat
py -3.13 -m venv .venv
```

The project requires Python 3.13 (`pyproject.toml`: `>=3.13,<3.14`). `SETUP.bat` performs
steps B–D.

## C. Install the pinned requirements

```bat
%PY% -m pip install --upgrade pip
%PY% -m pip install -r requirements.txt
%PY% -m pip check
```

`MetaTrader5==5.0.6180` installs only on Windows. That is expected.

## D. Run the migrations

```bat
%PY% -m adaptive_scalper.cli doctor
```

Opening the database migrates it to schema 27. `doctor` prints `schema=27`. Back the
database up first if it already holds data (the Windows session used SQLite's online
backup API into `data\backups\`).

**Server clock (BUG_BACKLOG #14).** MT5 stamps every time with the broker's server
clock. `[mt5] server_time_rule` in `config/default.toml` is `"UTC+2/US_DST"`, measured
on the IC Markets DEMO terminal (UTC+3 while US DST is in effect). `doctor` prints
`mt5 server clock vs UTC:` per symbol; `VERIFIED` means the rule matches live quotes.
A database that already held MT5 rows before schema 27 is marked `SERVER_UNCONVERTED`
and the runtime, history and research commands refuse it until you run, once:

```bat
%PY% -m adaptive_scalper.cli history convert-server-time
```

It backs the database up to `data\backups\` first and converts the stored bars, ticks,
broker history and history cursors in one transaction.

## E. Byte-compile

```bat
%PY% -m compileall -q adaptive_scalper
```

## F. Run the full test suite

```bat
%PY% -m pytest -q
```

In the cloud the result was 1451 passed, with the 7 live-MT5 tests skipped. With the
terminal running on the laptop, those 7 run too; see step G.

## G. Run the Windows-specific tests

```bat
powershell -ExecutionPolicy Bypass -File scripts\windows_verify.ps1
```

This script is read-only against the broker. It checks:
- the Python version and `pip check`;
- `import MetaTrader5`;
- compileall;
- `doctor` and `symbols` (which also captures the broker symbol specs);
- `okf validate`;
- the full suite, **plus `tests/test_mt5_gateway_live.py`**;
- the kill-switch state.

Also run each launcher once and check its window (see `docs/WINDOWS_DEPLOYMENT.md`). The
cloud verified the launchers statically only.

## H. Launch MT5 manually

Start the MetaTrader 5 terminal yourself and log in to the **DEMO** account. Enable
**Algo Trading** in the terminal only when you reach step U; PAPER does not need it.

## I. Verify that the connected account is DEMO

```bat
%PY% -m adaptive_scalper.cli doctor
```

Required output:
- `mt5: reachable, account trade mode DEMO`
- `real-money execution: DISABLED`

If it reports REAL or CONTEST, **stop here**. The runtime will refuse to start anyway.

## J. Run the doctor, status and symbol checks

```bat
%PY% -m adaptive_scalper.cli status
%PY% -m adaptive_scalper.cli symbols
%PY% -m adaptive_scalper.cli strategies
```

All three of XAUUSD, GBPJPY and BTCUSD should report `resolved: true` and
`spec_captured: true`. A symbol the broker does not offer is excluded, never substituted.

## K. Run read-only reconciliation and health checks

```bat
%PY% -m adaptive_scalper.cli reconcile
%PY% -m adaptive_scalper.cli health
%PY% -m adaptive_scalper.cli why-no-trade
```

On a fresh account, `reconcile` should report `status: CLEAN`. It reads broker truth and
repairs **local** state only; it never sends an order. `health` reports `TRADING_BLOCKED`
until the kill switch is bootstrapped. That is correct.

## L. Perform ONE non-executing DEMO `order_check`

```bat
%PY% -m adaptive_scalper.cli order-check-probe --symbol XAUUSD --direction BUY
```

Before calling `order_check()` with the broker's minimum volume, the probe freshly verifies:
- DEMO account, terminal connected, and terminal and account trading permission;
- symbol identity and quote freshness;
- direction permission;
- a risk-safe volume;
- the filling type.

The order is **never sent**. The probe has no send path, and an architecture test enforces
that.

## M. Record the actual `order_check` retcode convention

The probe prints and stores the following in the `order_check_probes` table:
- `retcode` and `comment`;
- `margin_required`;
- broker company and server;
- terminal build;
- symbol and timestamp.

Copy the `retcode` into **BUG_BACKLOG.md item 5**:
- If it is `0` or `10009`, the default `DEFAULT_ORDER_CHECK_SUCCESS_RETCODES = {0, 10009}` is
  confirmed.
- If it is any other "success" code, stop and report it. The execution service would
  (safely) block every entry until the convention is updated.

## N. Do NOT `order_send` yet

## O. Run real historical backtests

```bat
%PY% -m adaptive_scalper.cli history bootstrap
%PY% -m adaptive_scalper.cli history status
%PY% -m adaptive_scalper.cli backtest --symbol XAUUSD --start 2021-01-01 --end 2023-12-31
%PY% -m adaptive_scalper.cli walk-forward --symbol XAUUSD --start 2021-01-01 --end 2023-12-31 --folds 5
%PY% -m adaptive_scalper.cli path-stress --symbol XAUUSD
%PY% -m adaptive_scalper.cli purged-validation --symbol XAUUSD --folds 5
```

Repeat for GBPJPY and BTCUSD. Every backtest range is recorded as VALIDATION data.

**Reserve a later range that you never backtest**, for example the most recent 6 months,
and run `oos` on it exactly once, when you are ready to judge:

```bat
%PY% -m adaptive_scalper.cli oos --symbol XAUUSD --start 2024-01-01 --end 2024-06-30
```

It refuses a range that overlaps anything already used, and refuses a second run. Results
use `UNVERIFIED_ASSUMPTION` costs until you configure `[costs.SYMBOL]` from evidence (step S).

## P. Run real PAPER mode on MT5 market data

```bat
%PY% -m adaptive_scalper.cli kill-switch bootstrap --operator-id YOUR_NAME --reason "PAPER burn-in start"
"START PAPER.bat"
"START DASHBOARD.bat"
```

The bootstrap is a human decision. Without it, PAPER runs but blocks every new simulated
entry, which is also a valid first run. PAPER never calls `order_check` or `order_send`.
Open the dashboard at http://127.0.0.1:8765/.

## Q. Restart PAPER mid-session and prove state continuity

While a simulated position or pending entry is open (dashboard → positions → paper
sessions):
1. Press **Ctrl+C** in the PAPER window.
2. Wait at least one bar (5 minutes).
3. Run `START PAPER.bat` again.

Verify:
- The session resumes with the same `equity` and the same open or pending state.
- There is no duplicate trade in `paper_trades`.
- `last_processed_bar_time_utc` advances by exactly the bars that closed while it was down.
- Any config change halts only that symbol's session, with the `PAPER_SESSION_CONFIG_MISMATCH`
  event. That is the intended protection; set `[runtime] paper_session_tag` to start a new
  session.

## R. Run a sustained PAPER burn-in

Track the evidence in the "Acceptance criteria" section below. Use the time the evidence
needs; the calendar alone does not decide.

## S. Review safety metrics and incidents

```bat
%PY% -m adaptive_scalper.cli why-no-trade --limit 100
%PY% -m adaptive_scalper.cli journal recent --limit 200
%PY% -m adaptive_scalper.cli news status
%PY% -m adaptive_scalper.cli rag stats
%PY% -m adaptive_scalper.cli costs observed
```

On the dashboard, check the events, orders and components panels. Any ERROR or CRITICAL
runtime event must have an explanation before you continue. Configure `[costs.SYMBOL]` in
`config/default.toml` from broker specifications or observed evidence. Unknown costs block
DEMO entries (`BLOCK_COST`) by design.

## T. Human decision about the kill switch

Bootstrap or clear the kill switch **only if appropriate**:

```bat
%PY% -m adaptive_scalper.cli kill-switch status
%PY% -m adaptive_scalper.cli kill-switch clear --operator-id YOUR_NAME --reason "..."
```

Nothing clears it automatically: not a launcher, not startup, not ML, RAG or OKF.

## U. Enable controlled DEMO

Check all of the following first:
- steps F, G, L and M passed;
- the PAPER burn-in is stable;
- `reconcile` is CLEAN;
- the orders panel shows no dangerous UNKNOWN;
- you have explicitly decided on the kill switch.

Then enable **Algo Trading** in the terminal and run:

```bat
"START DEMO.bat"
```

The banner must read:
- REAL-MONEY EXECUTION: DISABLED
- DEMO ACCOUNT REQUIRED
- NEW ENTRIES REQUIRE KILL SWITCH DISENGAGED

## V. Do not force a trade

There is no command that places a trade on demand, and none should be added.

## W. Wait for a natural proposal

Wait for a proposal that passes every gate. Each send re-verifies the following twice: before
`order_check`, and again before `order_send` (and then sends only the exact checked request):
- DEMO account and terminal/broker permission;
- canonical symbol and asset identity;
- direction and quote freshness;
- kill switch, reconciliation and UNKNOWN orders;
- duplicate and re-entry checks;
- news;
- cost and expected edge;
- portfolio risk, hard risk, daily loss and drawdown;
- margin and broker constraints;
- `order_check`.

## X. Verify the first DEMO outcome manually

Compare the MT5 terminal's Trade and History tabs with:

```bat
%PY% -m adaptive_scalper.cli journal recent --limit 50
%PY% -m adaptive_scalper.cli reconcile
%PY% -m adaptive_scalper.cli costs observed --symbol XAUUSD
```

Check:
- the position ticket, volume, stop and target match;
- the journal chain runs `SIGNAL_CREATED → … → ENTRY_ALLOWED ×2 → ORDER_SUBMITTED →
  ORDER_FILLED → POSITION_OPENED`;
- an `execution_cost_observations` row exists with the fill price, slippage and commission;
- `reconcile` is CLEAN.

To stop new entries at any time, run `STOP TRADING.bat`. It engages the kill switch and does
**not** close positions.

## Y. Package the Windows release only after QA succeeds

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1 -Version 0.1.0
powershell -ExecutionPolicy Bypass -File scripts\release_smoke_test.ps1 -Zip dist\AdaptiveScalperNext-0.1.0.zip
```

See `docs/RELEASE.md`.

---

## Acceptance criteria for local DEMO

These are evidence-based; elapsed days alone are not a criterion. Every item must hold:
- zero real-money mutation;
- zero unexplained safety bypass;
- zero unresolved dangerous UNKNOWN before new exposure;
- correct reconciliation;
- correct restart recovery (step Q, and a DEMO restart with an open position);
- correct pending-order handling;
- correct open-position management (stop advances journaled, and no widening);
- correct kill-switch behavior (STOP TRADING blocks new entries and keeps managing
  positions);
- one-new-bar PAPER progression;
- session and config fingerprint protection;
- real costs explicitly known, or clearly degraded;
- no unhandled runtime exceptions (no `TASK_FAILED` events left unexplained);
- no database corruption (`doctor` shows `integrity=ok`);
- consistent journal chains;
- dashboard failure isolation (kill the dashboard; the runtime is unaffected);
- correct CLI and operator commands.

Track the following. The dashboard and `why-no-trade` give most of them:
- runtime hours;
- completed bars;
- PAPER decisions;
- blocked decisions by reason;
- simulated trades;
- restarts;
- market-session transitions;
- spread shocks;
- news blocks;
- reconciliation cycles;
- fault recoveries.

Do not fabricate evidence. If something did not happen during the burn-in, record that it was
not observed.

## Items the cloud could not verify

Each one is BLOCKED-ON-LOCAL-MT5 or needs Windows:

| Item | Where |
|---|---|
| `tests/test_mt5_gateway_live.py` (7 tests) | step G |
| `MetaTrader5` import and version pin | steps C and G |
| Running the `.bat` launchers and `.ps1` scripts (verified statically in the cloud) | steps G and Y |
| Real `order_check` success-retcode convention (BUG_BACKLOG #5) | steps L and M |
| Broker timestamp convention: server time vs UTC (BUG_BACKLOG #14) | steps J and P: compare the tick time with UTC |
| Reconciliation broker-symbol translation on a real account (logic fixed and tested, BUG_BACKLOG #7) | step X |
| Real historical data, backtests and OOS | step O |
| PAPER on live data, restart continuity, burn-in | steps P–R |
| DEMO execution-cost evidence | steps S and X |
| First natural DEMO order and reconciliation | steps W and X |
| Release zip build and smoke test | step Y |
