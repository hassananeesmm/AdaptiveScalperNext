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
- MULTI-POSITION READINESS (Strategy Lab tab "Multi-position readiness", Safety & risk, All panels;
  `/api/panels/multi_position`):
  position capacity (max/open/pending/remaining slots), open and pending monetary risk, remaining
  aggregate-risk capacity from the fresh DEMO equity, enabled / awaiting-market / excluded symbols,
  every open position with the strategy that opened it (local position record), its broker position id,
  initial risk and live floating P&L (N/A without a fresh broker sample), money in the account currency,
  per-symbol quote (with age), signal, latest decision and its strategy, cost-evidence and news status,
  pairwise correlation (value or N/A, aligned
  sample count, threshold, ALLOW/BLOCK, reason) and one status per symbol explaining why a second trade has
  not opened: NO SIGNAL, AWAITING COMPLETED BAR, EXISTING POSITION, STALE QUOTE, MARKET CLOSED,
  BLOCKED BY CORRELATION, BLOCKED BY COST, BLOCKED BY NEWS, BLOCKED BY RISK (incl. portfolio heat and
  max positions), BLOCKED BY MARGIN, BLOCKED BY RECONCILIATION, ELIGIBLE FOR A NATURAL SIGNAL, plus
  UNKNOWN ORDER, KILL SWITCH, RE-ENTRY RULE, BROKER BLOCK and NOT ENABLED. Each symbol also shows its bar
  status (AWAITING COMPLETED BAR while the runtime waits for the next close), the strategy signals of its
  most recent decided bar, and the LAST ACTUAL BLOCK (the latest decision where a signal existed but a
  gate stopped it, with its time). Each open position has a button that opens its DEMO trade lifecycle in
  Strategy Lab. XAUUSD and BTCUSD are both USD-quoted, so one high-impact USD release blocks both at once
  (tested). Gate blocks are named from the
  journal's `ENTRY_BLOCKED` decision code, not parsed from text. A free slot is shown as capacity, never
  as a forecast. Read-only: runtime_state + SQLite, no MT5 call, no order path.
- At 1920x1080 the layout has a left navigation and two panel columns.
  At 1366x768 metrics use two rows; below 1080 CSS pixels panels stack.
  Below 740 CSS pixels navigation becomes horizontal and scrollable.
- Fluid sizing, min-width:0, scrollable tables, large click targets,
  visible keyboard focus, and reduced-motion support accommodate Windows
  browser resizing and display scaling (100%, 125%, 150%).
- Verified 2026-09-25 in a real Chromium (Playwright) against the live PAPER runtime,
  every view at CSS viewports 1366x768, 1600x900, 1920x1080, 2560x1440 and the scaled
  equivalents 1093x614 (1366@125 %), 911x512 (1366@150 %), 1280x720 (1600@125 %,
  1920@150 %), 1536x864 (1920@125 %), 1707x960 (2560@150 %): no page-level horizontal
  scroll, no header overlap, no sidebar overflow, tables scroll inside their containers,
  all navigation reachable, minimum font 11 px, keyboard focus visible, light theme OK.
  Limitation: viewport emulation, not the Windows display-scaling setting itself.

## What live means

1. The existing synchronized MT5 gateway belongs ONLY to the trading
   runtime. The runtime publishes a read-only MT5 telemetry snapshot into
   runtime_state approximately every 5 seconds. It contains account
   balance/equity/free margin and currency, DEMO account mode, connection,
   quotes with UTC broker timestamps, and (DEMO mode only) broker positions.
   The account login and credentials are not persisted in the snapshot.
2. FastAPI pushes the latest database panels over WebSocket about every
   2 seconds, with 3-second GET polling fallback and reconnection. All panels are
   read from ONE SQLite snapshot and aged against a time taken after it (ages are
   never negative). A refresh takes ~0.05 s on the laptop's 218 MB database; the
   full `PRAGMA integrity_check` (~6 s there) runs in a background monitor at most
   every 10 minutes, and the overview shows its result and age (PENDING until the
   first check finishes).
   If the dashboard's own data is older than 15 s or the server is unreachable,
   the summary shows runtime/health UNKNOWN, broker figures "—", the kill switch
   "(last known)" and a banner with the data age; the page reconnects by itself.
3. A working browser WebSocket DOES NOT prove MT5 is connected. Broker
   telemetry older than 15 seconds, disconnected terminals, and quotes
   older than 15 seconds are displayed as stale, not live. The panel reports a quote
   as FRESH or STALE (the browser labels FRESH "LIVE FEED"); the word LIVE is kept out
   of the source because a safety audit forbids any LIVE execution-mode string. Closed-market
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

The broker server-clock rule and historical time-basis conversion are
done by the runtime gateway (BUG_BACKLOG #14). The dashboard reports their
verified status without attempting its own time conversion.

Top summary cards: account equity, floating P&L, DEMO booked net today,
broker positions, dangerous UNKNOWN orders, feed freshness, account balance,
MT5 & account (connection + trade mode), and total risk (local open + pending
risk as % of fresh equity, against the 0.75 % ceiling). Header pills: real-money
execution DISABLED, health, runtime mode, kill switch, dashboard feed state.

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
