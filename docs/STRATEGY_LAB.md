# Strategy Lab

Strategy Lab is a top-level view in the existing observer-only dashboard
(`docs/DASHBOARD.md`). It answers, from real recorded evidence only: which
strategy generated a signal, which was selected, which the broker actually
executed, which strategies show positive or negative net results, why a
trade won or lost, and which strategies generate signals but never trade.
It is read-only: it cannot submit an order, modify a stop, clear the kill
switch, or activate/retire a strategy. Changing which strategies are active
is a separate reviewed code/config change with tests and an operator-approved
deployment (`config/default.toml`'s `[strategies]` table and
`RETIRED_STRATEGY_KEYS`), never a dashboard interaction.

Backend: `adaptive_scalper/dashboard/panels.py` (`strategy_registry`,
`strategy_activity`, `strategy_attribution`, `strategy_performance`).
Frontend: `adaptive_scalper/dashboard/page.py`. Panels are served at
`/api/panels/<name>` and pushed over the same WebSocket/polling channel as
every other panel (`docs/DASHBOARD.md`'s "What live means").

## Where each panel's data comes from

**Strategy registry** (`strategy_registry`). Strategy identity and entry
logic are never hand-copied text that could drift from the code: `version`,
`source_module`, `source_description` (the module's own docstring) and
`default_parameters` (the strategy class's actual `__init__` defaults) are
read live via `inspect` from `adaptive_scalper.strategies.build_active_registry()`.
Retired strategies (`failed_breakout_fade`, `support_resistance_reaction`,
directive section 8) are reported separately with `registration_status:
RETIRED_PERMANENTLY` and no source module — the retirement firewall means
none remains registered to introspect.

`lifecycle_stage` is derived, in order, from the most advanced real evidence
found in the last 30 days: `RETIRED` > `FILLED` (an OPEN broker position
exists) > `CLOSED` (latest position is CLOSED) > `SUBMITTED` (an
`ENTRY_ALLOWED` journal event exists) > `SELECTED` (a `PROPOSAL_CREATED`
event exists) > `PROPOSED` (a `SIGNAL_CREATED` event exists) > `REGISTERED`
(none of the above — the strategy exists but has done nothing observable).
A `REGISTERED` or `PROPOSED` strategy is never described as validated or
profitable; it has not necessarily traded at all. `counts_last_30d` (signals
created/rejected, proposals selected, entries allowed/blocked) come from
`journal_events`, one row per real decision the runtime made — never
re-derived or estimated.

**Strategy activity** (`strategy_activity`). The 200 most recent rows of
`entry_decisions` — the complete recorded `reason`/`detail_json` for every
real evaluation, never truncated or re-summarized. This is the "why no
trade" evidence: a strategy can appear here proposing a direction that was
never selected, or selected and then blocked before submission.

**Strategy attribution** (`strategy_attribution`). Broker-verified DEMO
trade attribution. The broker is authoritative (directive section 31): for
every locally-tracked `positions` row, every `deals` row sharing its
`broker_position_id` is summed exactly once for gross P&L
(`sum(profit)`) and cost (`sum(commission + swap + fee)`) — a position is
never counted twice, and an entry deal is never counted as a realized
result on its own. `entry_deal_count` / `closing_deal_count` are reported
directly so a partial fill or multiple closing deals are visible
(`partial_fill_or_multi_close`) rather than silently averaged away. Any
`deals` row whose `broker_position_id` does not match a locally-tracked
position is returned separately under `unattributed_deals` — it is never
assigned to a strategy by matching symbol, direction or timestamp.

**Strategy performance** (`strategy_performance`). Row-level closed trades
for three separate evidence classes, each its own currency of truth and
**never pooled into one figure**: `demo_closed_trades` (from `positions`
joined to `deals`, broker-verified), `paper_trades` (from `paper_trades`,
simulated), `backtest_trades` (from `backtest_trades`, tagged with
`run_id`). Each is capped at the 300 most recent rows to keep the panel
cheap on the dashboard's ~2 s refresh; `sample_sizes` in the response is the
actual row count shipped, so the UI's "N" always reflects what it actually
received, not a hidden total.

## How the comparison table is built (client-side)

The comparison table, charts and CSV/JSON export all read from the same
`strategy_performance` response and the same active filters
(`page.py`'s `stratPerformancePanel`/`aggregateTrades`) — there is no
separate, potentially-inconsistent code path for tables vs. charts.

- **Evidence tabs**: DEMO / PAPER / BACKTEST. Switching tabs swaps the
  entire row set; nothing from one tab is mixed into another tab's
  aggregates.
- **Filters**: strategy, symbol, direction, regime, strategy version,
  cost provenance, and a from/to date range — applied to the row set
  before aggregation, so every derived number (win rate, profit factor,
  P&L, drawdown) reflects only the filtered rows.
- **Win rate** is `null` (shown as "—") when a strategy has zero rows in
  the filtered set — never `0%`. It is never computed by combining
  BACKTEST and DEMO rows.
- **Profit factor** is gross profit / gross loss when gross loss > 0;
  `Infinity` (shown as "∞") when there are wins and zero losses; `null`
  ("—") when there are zero trades. It is never silently shown as `0` or
  omitted.
- **Sample size (N)** is shown on every comparison row, always. A strategy
  with one or two closed trades is not described as consistently
  profitable anywhere in the UI — the table shows the raw count and lets
  the operator judge it.
- **Max drawdown** is computed only from the currently filtered,
  chronologically-ordered trade sequence for that strategy — it changes
  with the filters and is labelled as such.
- **Charts** (per-strategy cumulative net P&L, gross vs. cost, results by
  symbol, results by regime, trade frequency over time) all read the same
  filtered set used for the table; a losing trade is never dropped from a
  chart while remaining in the table.

## Partial fills, partial closes and costs

A `positions` row's `entry_deal_count` / `closing_deal_count` coming back
greater than 1 means a genuine partial fill or multiple closing deals — the
panel reports the deal counts and the flag rather than guessing at a single
clean entry/exit. Realized net P&L for a closed position is
`gross_pnl + costs`, where `costs` sums commission, swap and fee across
every deal on that position exactly once. An entry deal alone is never
reported as a realized win or loss — only a `CLOSED` position's summed
deals produce a realized figure; an `OPEN` position's floating result is
kept out of the closed-trade tables entirely (see `positions` panel for
unrealized exposure).

## What UNATTRIBUTED means

A broker deal is UNATTRIBUTED when its `broker_position_id` does not match
any locally-tracked `positions` row. This can happen for a manual/external
broker-side action, a position opened before local tracking began, or a
genuine data gap. UNATTRIBUTED deals are listed with their available
broker fields (price, volume, commission, swap, profit, fee, deal type,
timestamp, comment) so the operator can judge them directly — they are
never guessed onto a strategy by matching symbol, direction or timing, and
they are never silently dropped from the reconciliation total.

## How PAPER, DEMO and BACKTEST differ

- **DEMO** — real IC Markets DEMO broker orders and deals, read from the
  local database that mirrors broker truth via reconciliation. This is the
  only evidence class that reflects actual (simulated-money) execution.
- **PAPER** — the PAPER runtime's own simulated fills against live market
  data (`docs/DASHBOARD.md`); never broker-confirmed, always labelled
  PAPER.
- **BACKTEST** — historical evaluations tagged with their own `run_id`,
  dataset and cost assumptions (`backtest_runs`); a historical result, never
  a stand-in for a missing DEMO or PAPER result. The protected out-of-sample
  period is never used to select or tune a strategy shown here.

These three are shown on separate tabs and are never combined into one
equity curve, one win rate, or one net-P&L figure.

## Interpreting a small sample

Every comparison row carries its own N. A handful of closed DEMO trades is
not evidence of a validated edge in either direction — treat DEMO figures
with N in the single digits as a live sanity check, not a verdict. Compare
against the strategy's own PAPER/BACKTEST evidence and its signal-to-entry
funnel on the registry panel before drawing a conclusion: a strategy with
positive DEMO net P&L but a very small N (or a strategy with a high win
rate but negative net P&L once costs are included) is exactly the case
this panel is built to surface rather than hide.

## Using the filters and shortlist

Set the evidence tab first, then the strategy/symbol/direction/regime/
version/provenance filters and date range — the table, charts and CSV/JSON
export all update together. Check up to three strategies' "Compare" boxes
to build a **Human Strategy Review shortlist**; the shortlist is stored in
the browser (`localStorage`, not the database),
survives a page reload, and can be exported as JSON or CSV for offline
comparison. Building or exporting a shortlist never changes the running
system: promoting, demoting or reconfiguring a strategy is a separate,
reviewed change outside the dashboard.
