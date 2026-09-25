# Windows dashboard: live observer

The dashboard is a browser-based, observer-only interface at http://127.0.0.1:8765/.
It opens the existing SQLite database READ-ONLY and never connects directly to MT5.
There are no trade buttons, settings mutation endpoints or kill-switch controls.
The PAPER or DEMO runtime must run separately on the Windows laptop.

## Windows layout and navigation

- Dark default and optional locally saved light theme; system Segoe UI font;
  no external fonts, APIs, images or CDN.
- Seven views: Overview, Markets, Trading, Safety & risk, Research, System,
  and All panels. Panel-name search is available across views.
- At 1920x1080 the layout has a left navigation and two panel columns.
  At 1366x768 metrics use two rows; below 1080 CSS pixels panels stack.
  Below 740 CSS pixels navigation becomes horizontal and scrollable.
- Fluid sizing, min-width:0, scrollable tables, large click targets,
  visible keyboard focus, and reduced-motion support accommodate Windows
  browser resizing and display scaling (100%, 125%, 150%).
- Real browser checks for each scaling setting are still required.

## What live means

1. The existing synchronized MT5 gateway belongs ONLY to the trading
   runtime. The runtime publishes a read-only MT5 telemetry snapshot into
   runtime_state approximately every 5 seconds. It contains account
   balance/equity/free margin and currency, DEMO account mode, connection,
   quotes with UTC broker timestamps, and (DEMO mode only) broker positions.
   The account login and credentials are not persisted in the snapshot.
2. FastAPI pushes the latest database panels over WebSocket about every
   2 seconds, with 3-second GET polling fallback and reconnection.
3. A working browser WebSocket DOES NOT prove MT5 is connected. Broker
   telemetry older than 15 seconds, disconnected terminals, and quotes
   older than 15 seconds are displayed as stale, not live. Closed-market
   quotes may correctly remain stale.
4. The runtime heartbeat is independently stale after 15 seconds. Missing
   data is marked NO_DATA/UNAVAILABLE; the dashboard never fabricates
   trades, prices, profitability or current position values.
5. The telemetry task is lower priority than position reviews and entry
   checks, but it shares the single-threaded scheduler; monitor duration
   and do not mistake the sample cadence for a tick-level stream.

## Panel coverage and source truth

| Panel | Source |
| --- | --- |
| System overview | Runtime heartbeat, health and persistent kill switch |
| Live market & account | Last runtime MT5 snapshot, quote ages, broker position snapshot and configured server-clock verification |
| Observed performance | Last 30 days of DEMO booked net and SEPARATE per-symbol PAPER outcomes |
| Components | Runtime-reported component health |
| Symbols | Validated canonical mappings, captured specifications and latest regime |
| Positions | Local DEMO open positions and independent PAPER sessions |
| Orders | Active orders, unresolved UNKNOWNs and reconciliation snapshot |
| Decisions | Entry decisions and reasons during last 24 hours |
| Risk | Actual configured limits, local estimated open/pending exposure, fresh broker equity and approximate drawdown |
| News | Cached upcoming high-impact events and live calendar health |
| Events | Runtime conditions and unresolved execution incidents |
| Costs | Observed execution-cost evidence |
| Research | Research runs, trials and spent OOS ranges |
| Learning | Stage-1 observer models; zero execution authority |
| Memory | RAG memory counts and ingestion watermark |
| Knowledge | Curated OKF bundle health, advisory-only |
| History | Bar coverage |

PAPER and DEMO evidence MUST NOT be pooled. PAPER still has independent
per-symbol simulated equity, not pooled portfolio equity. DEMO booked net
comes from locally observed deals (profit, commissions, fees and swap),
not a guarantee that the journal contains the broker's complete history.
Risk utilization uses recorded initial/remaining exposure and should
be confirmed against broker stops and reconciliation.

The broker server-clock rule and historical time-basis conversion live
in the windows-validation branch. The dashboard reports their verified
status without attempting its own time conversion.

## Operating instructions

Run, from C:\AdaptiveScalperNext in PowerShell:

    .\.venv\Scripts\python.exe -m pytest -q tests/test_dashboard_panels.py tests/test_dashboard_live_ui.py
    .\.venv\Scripts\python.exe -m pytest -q
    .\START DASHBOARD.bat

Separately start START PAPER.bat or, only after all Windows/MT5 safety
checks and explicit human kill-switch authorization, START DEMO.bat.

Open http://127.0.0.1:8765/. Test browser widths of 1366x768 and
1920x1080 at 100%, 125%, 150% display scaling. Verify scrollable
tables, navigation, data freshness, panel failures and responsiveness.
Stop/restart the dashboard without stopping trading runtime, then stop
the runtime and verify the dashboard marks its heartbeat and broker
snapshots stale. Do not label any live verification passed before it
actually runs on the Windows machine.

No dashboard endpoint may modify orders, positions, the kill switch,
broker credentials, trading configuration or runtime schedules.
