# Strategy Lab

Strategy Lab is the top-level **Strategy Lab** view of the observer-only
dashboard. It answers, from durable recorded evidence only:

- which strategy generated each signal, was selected, submitted the broker
  order, owns each fill, open position and closed trade;
- which strategies have positive or negative realized net results, and what
  commission, fees, swap, spread and slippage each incurred;
- why each trade was opened, managed and closed;
- which strategies signal but never execute, and why;
- which broker trades cannot be reliably attributed.

It is **review-only**. It cannot place, modify or close an order, change risk,
touch the kill switch, or activate, disable or promote a strategy. The review
shortlist lives only in the browser. Changing the active strategy set is a
separately reviewed code/config change with tests and an operator-approved
controlled deployment.

| Piece | Where |
|---|---|
| Evidence engine (attribution, accounting, metrics, reconciliation) | `adaptive_scalper/dashboard/strategy_lab.py` |
| HTTP (GET only, computed on demand) | `/api/strategy-lab/summary`, `/trades`, `/trade/{evidence}/{id}`, `/strategy/{key}`, `/export.csv` in `adaptive_scalper/dashboard/app.py` |
| UI | Strategy Lab view in `adaptive_scalper/dashboard/page.py` |
| Tests | `tests/test_strategy_lab_attribution.py`, `tests/test_dashboard_strategy_lab.py` |

The Lab is **not** part of the 2-second WebSocket push. It is computed when
the view is open (at most every 20 s while visible, or on Reload/filter
change) from one read-only SQLite snapshot. The DEMO ledger is cached in
memory, keyed by a database fingerprint (database file, `MAX(rowid)`/`COUNT(*)`
of every source table, position close markers). Any new deal, order, journal
event or position change produces a new fingerprint and a fresh computation.
Every response shows when it was computed and whether it was served from
cache ("database unchanged").

## Evidence sources: DEMO, PAPER, BACKTEST

Three independent tabs. **They are never pooled into one figure.**

- **DEMO**: actual broker execution evidence for one broker account (default:
  the account holding the most recent imported deal). Money comes only from
  broker-recorded deals.
- **PAPER**: rows of `paper_trades` (simulated fills on live data). Sessions
  are independent per-symbol simulations. Statistics are per trade and never
  presented as one portfolio equity curve. A session selector narrows to one
  session.
- **BACKTEST**: exactly one research run at a time (`backtest_runs.run_id`,
  default: the most recent). The run selector shows its symbol, trade count
  and cost provenance. It is a historical research evaluation, not evidence
  of current live performance. The reserved out-of-sample interval is never
  run from the dashboard.

## The DEMO deal population

The population for an account is the union of:

1. every deal in `broker_account_deals` for that login (the imported broker
   history, `broker-history import`), and
2. every deal the runtime recorded itself in `deals` (broker-confirmed at
   execution) whose ticket is not in that import.

Deals are **de-duplicated by broker deal ticket**. When both sources hold a
ticket, the broker-history values are used and any disagreement in profit,
commission, swap, fee or volume is **listed** in the Reconciliation view
(never silently resolved).

Non-trade deals (deposits, balance/credit corrections: MT5 deal types other
than BUY/SELL) are reported separately and are never counted as trades.

## Attribution rules

A broker position is **ATTRIBUTED** to a strategy only when **all** of these
durable, runtime-written records agree:

1. a local `positions` row with that exact broker position id;
2. its `entry_order_id` resolves to an `orders` row whose `chain_key` is a
   runtime entry chain (`entry:<symbol>:<bar>:<strategy>`, the only chain
   family the DEMO runtime writes);
3. that chain contains a `PROPOSAL_CREATED` journal event whose
   `strategy_key` equals `positions.strategy_key`;
4. when present, `position_entry_context` for the position names the same
   strategy and chain (it is also the source of strategy version and entry
   regime);
5. when the broker entry deal is known, its order ticket equals the local
   order's `broker_order_id`;
6. a broker entry (IN) deal exists in the population.

The trade lifecycle shows every check with PASS/FAIL. **Symbol, direction,
timestamp proximity and broker comments are never used to assign a
strategy.**

Everything else is **UNATTRIBUTED**, split by source class:

| Source class | Meaning (evidence) |
|---|---|
| `ASN_UNATTRIBUTED` | This runtime's own trade (local record, or a magic number this runtime stamped on its orders) but at least one chain link above failed. The failing link is named. |
| `MANUAL` | Broker `DEAL_REASON` is CLIENT, MOBILE or WEB. |
| `EXTERNAL_EXPERT` | A magic number this runtime never used, or broker `DEAL_REASON` EXPERT without our magic. |
| `UNKNOWN_SOURCE` | Magic 0 and no broker `DEAL_REASON` recorded. Consistent with manual activity, but not proven. |

The runtime's magic numbers are read from `orders.magic` (what it actually
stamped), never hard-coded. `DEAL_REASON` is recorded by the broker-history
importer from migration 0029 onward. Rows imported earlier have `reason = NULL`
and are **not** back-filled; re-importing a range stores nothing new for
existing tickets (`INSERT OR IGNORE`).

Provenance: every trade lists `recorded_by` (`BROKER_HISTORY_IMPORT`,
`LOCAL_RUNTIME_RECORD`, or both).

## Accounting

Per broker position, over its de-duplicated deals:

```
gross     = sum(profit)                       broker-recorded; spread and slippage are already inside it
costs     = sum(commission) + sum(fee) + sum(swap)
net       = gross + costs
realized  = net - entry_commission_and_fee x (open volume / entry volume)
open-volume entry costs = entry_commission_and_fee x (open volume / entry volume)
realized R = realized / initial_monetary_risk     CLOSED trades with recorded risk only
```

- **Status.** `CLOSED` when exit volume ≥ entry volume, `PARTIALLY_CLOSED`
  when some exit volume exists, `OPEN` when none. `ENTRY_NOT_IN_HISTORY`
  (entry deal outside the imported range), `REVERSAL_UNSUPPORTED` (INOUT
  deal) and `UNKNOWN_DEAL_ENTRY` are shown, never hidden.
- **Only CLOSED trades count** in wins/losses/win rate/profit factor/R. An
  entry is never a completed trade.
- **Multiple entry fills** are volume-weighted into one entry price. Multiple
  exit deals are summed. Each deal is counted exactly once.
- **Spread and slippage** (DEMO) come from `execution_cost_observations`
  (spread at entry, entry slippage, price units) and
  `position_management_state.realized_slippage` (exit). They are **evidence
  only**: they are already inside broker profit and are never subtracted
  again. For PAPER/BACKTEST they are separate simulated costs.
- **Currency.** DEMO money is in the broker account currency last sampled by
  the runtime (`runtime_state.live_telemetry.account.currency`). If none was
  recorded the UI says "currency not recorded"; it never assumes USD.
  PAPER/BACKTEST are simulated in the account currency of the captured
  broker specs.
- **Unknown costs** stay `None`/N/A (for example a research trade without
  a recorded gross or commission). They are never shown as 0.
- **Floating P&L** of open positions is shown on the Overview (runtime
  telemetry). The Lab ledger is realized-only.

## Metrics

Computed over CLOSED trades in the currently filtered set:

| Metric | Definition |
|---|---|
| Wins / losses / breakevens | realized net > +0.005 / < −0.005 / otherwise |
| Win rate | wins / closed. **NO CLOSED TRADES** (not 0 %) when none. |
| Profit factor | sum(winners) / abs(sum(losers)). With no losses: `NO_LOSSES` (undefined, never infinite). |
| Average R (n) | mean realized R over trades with recorded initial risk; n shown |
| Expectancy | realized net / closed trades |
| Max drawdown | peak-to-trough of the cumulative realized net over the closed-trade sequence ("closed-trade basis"), not an equity drawdown |
| Gross profit (winners) / gross loss (losers) | sum of the realized NET result of the winning / losing closed trades (their net already includes that trade's commission, fees and swap, so costs are never subtracted twice); profit factor = profit / abs(loss) |
| Open exposure | sum of initial monetary risk of this strategy's open/partially-closed positions |
| Unrealized (live) | DEMO only: the broker's floating P&L of this strategy's open positions from the runtime's sampled broker snapshot (<= 15 s old, terminal connected). Shown beside realized results and never added to them. `NO_OPEN_POSITIONS`, `NO_FRESH_BROKER_SAMPLE` or `POSITION_NOT_IN_BROKER_SAMPLE` instead of a guessed number; PAPER/BACKTEST show `SIMULATED` |

Rows stay in registry order and are never ranked. Every row shows its sample
size. Rows with few trades are an insufficient sample, not a result.

## The DEMO funnel (signal → order → fill)

Counted **only from runtime entry chains** (`entry:` prefix):

- signals = `SIGNAL_CREATED`; rejected = `SIGNAL_REJECTED`;
  selected = `PROPOSAL_CREATED`;
- allowed / blocked = chains with `ENTRY_ALLOWED` / `ENTRY_BLOCKED`
  (attributed through the chain's proposal);
- order rows = `orders` created for that chain;
- **submitted = orders with a `SUBMITTED` state transition**;
- filled = orders with `filled_volume > 0`.

An order row that never reached SUBMITTED was stopped by a permission gate
(for example re-entry churn) **before any broker call** and is not a
submitted order. Such rows remain in state `PROPOSED` by design of the order
state machine. Journal chains from other producers (the legacy 2026-09-17/18
`XAUUSD-<strategy>-<ts>-<hash>` replay chains, CLI reconcile chains) are
counted separately and excluded. They previously inflated the registry
panel's signal counts, for example `statistical_reversion` "161 signals" of
which 130 were legacy. Fixed in this release; the registry panel also counts
runtime chains only.

## Filters

Date range (UTC days; the end date is inclusive; closed trades filter by
close time, others by entry time), strategy (including `UNATTRIBUTED`),
symbol, strategy version, regime, direction, trading session (DEMO: session
recorded at execution; PAPER/BACKTEST: UTC-hour bucket of the entry),
exit reason, cost provenance, source class. All tables and charts use the
same filtered set. Losing trades are never silently excluded.

## Views

- **Comparison.** The winning/losing table (all six active strategies,
  always; unattributed row; retired strategies listed separately and never
  as candidates), then cumulative net per strategy, gross vs costs, and
  results by strategy/symbol/regime/direction/session/exit reason. Also
  trade frequency, the win/loss and R distributions, and signal→order→fill
  conversion. Every chart has a legend or direct labels and a table view.
  Colors follow the strategy, not its rank. The chart palette is validated
  for colour-vision deficiency in both themes.
- **Strategy detail.** Actual entry rules (`evaluate()` source, verbatim),
  eligible regimes (parsed from the regime gate in the source), current
  parameters of the registered instance, ATR stop/target multiples, required
  confidence, expected duration. Also the latest evaluation, latest proposed
  signal, last selected signal, latest broker-confirmed trade, open
  positions, recent closed trades and why-no-trade history.
- **Compare (max 3).** Side-by-side metrics and cumulative net under
  identical filters.
- **All trades.** Paginated (50 per page, server-side, max 200), searchable.
  Selecting a row opens the full lifecycle: signal, selection, permission
  checks, expected and observed execution costs, local order and state
  transitions, order journal events, broker orders, broker deals, position
  management state, management actions, reviews, advisory ML/RAG evidence
  (no authority) and accounting.
- **Unattributed.** The same list restricted to trades without a proven
  strategy, with totals by source class.
- **Reconciliation.** See below.
- **Review shortlist.** Browser-only list with CSV export (the comparison
  table under the current evidence and filters) and JSON export.

## Verifying that the per-strategy results reconcile with broker deals

The Reconciliation view (DEMO) recomputes the population total
**independently in SQL** (broker-history deals for the login, plus runtime
deals not in that import) and compares it with the ledger:

```
population_net_sql == sum over source classes of position net
                     + non-trade deals + trade deals without a position id
realized + open-volume entry costs == position net   (for every class)
deal count (SQL) == deal count (ledger)
```

`reconciles: true` requires all three to agree (money within 0.01, counts
exact). It covers the full population (all dates); filters narrow the tables
only. The closed-count check compares attributed CLOSED positions with local
`positions` rows marked CLOSED.

Manual verification, read-only, on a database copy:

```
python -c "from adaptive_scalper.persistence.database import connect_readonly as c; \
from adaptive_scalper.dashboard import strategy_lab as s; \
r=s.summary(c('data/backups/<copy>.sqlite3'),'DEMO',{},{})['reconciliation']; \
print(r['reconciles'], r['population_net_sql'], r['population_net_ledger'])"
```

Independent sanity check: with the full account history imported, the
population net equals the broker account balance (deposits are non-trade
deals). Measured 2026-09-26 on the pre-change backup: 2,272 deals, ledger
9,652.33 USD = SQL 9,652.33 USD = broker balance 9,652.33 USD.

### Measured on 2026-09-26 (backup `pre_strategy_lab_attribution_20260926T110252Z`)

| Class | Positions | Net (USD) |
|---|---|---|
| ATTRIBUTED (all `microstructure_acceleration`) | 25 closed | −55.52 (11W / 14L, PF 0.65, avg R −0.10) |
| EXTERNAL_EXPERT (magic 770115, pre-runtime history) | 1,101 closed | −4,576.72 |
| UNKNOWN_SOURCE (magic 0, reason not recorded) | 8 closed | +3,267.89 |
| ASN_UNATTRIBUTED / MANUAL | 0 | 0.00 |
| Non-trade deals (deposits) | 4 | +11,016.68 |

The other five active strategies: NO CLOSED TRADES on DEMO.
`statistical_reversion` produced 32 runtime signals and 4 selections, all
blocked before submission; momentum and pullback 4 signals each, all
rejected; range breakout and volatility expansion none.

## What remains unattributed and why

- Imported history before this runtime existed (other expert adviser,
  magic 770115) and magic-0 activity: correctly never attributed.
- Rows imported before migration 0029 have no `DEAL_REASON`, so magic-0
  activity is `UNKNOWN_SOURCE` rather than `MANUAL`.
- Until the runtime release carrying the close-magic fix is deployed, the
  runtime's own adaptive-exit closing deals carry magic 0. Attribution is
  unaffected (it follows the broker position id), but the broker copy alone
  cannot distinguish them from manual closes. The exit reason is then
  evidenced by the runtime's recorded exit request.

## Safe deployment and recovery

The dashboard can be restarted independently of the trading runtime. Its
failure never affects position management. Deploying this release
additionally involves:

1. **Migration 0029** (additive: `broker_account_deals.reason` + three
   indexes). Any CLI command of the new code migrates the database it opens,
   so do not run the new code's CLI against the production database while
   the old runtime is live. Apply it during a controlled restart after a
   verified online backup (see `docs/OPERATIONS_RUNBOOK.md`).
2. **Close-order magic** (`position_management/manager.py`,
   `runtime/demo.py`) takes effect only in a restarted runtime.
3. Recovery: the migration adds a nullable column and indexes only; restoring
   the pre-change backup fully reverts it. No historical row is modified.
