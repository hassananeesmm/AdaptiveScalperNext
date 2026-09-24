# Architecture

`MASTER_BUILD_DIRECTIVE.md` is authoritative. This document describes the code as it
stands.

## Processes

| Process | Command | Talks to MT5 | Writes SQLite |
|---|---|---|---|
| Runtime (PAPER or DEMO) | `paper` / `demo` | yes: the **only** MT5 client, one `SynchronizedGateway` | yes |
| Dashboard | `dashboard` | **never** | **never** (read-only connection) |
| Operator CLI | one-shot commands | only broker-facing commands, DEMO-verified first | yes |

A second runtime against the same database is refused while the first one's heartbeat is
fresh.

## Packages (`adaptive_scalper/`)

| Package | Responsibility |
|---|---|
| `config/` | TOML config (pydantic). `constants.py` holds the hard allow-lists: symbols, modes, retired strategies. |
| `persistence/` | `connect()` / `connect_readonly()`, ordered SQL migrations (0001–0026). |
| `core/` | Kill switch (fail-closed, audited), `OperatorAuthority`, composed final permission. |
| `gateway/` | `Gateway` protocol, `Mt5Gateway` (the only MetaTrader5 import), `FakeGateway`, `SynchronizedGateway`, `factory.py` (the only construction site), symbol resolution and validation, the DEMO gate, the spec store. |
| `history/` | Resumable bar and tick bootstrap, broker account history, coverage. |
| `features/`, `regimes/`, `strategies/` | Causal features, regime classification plus confirmation tracker, six strategies plus the retirement firewall. |
| `costs/` | Cost model, expected-net-edge gate, DEMO execution cost observations. |
| `portfolio/`, `risk/` | Correlation and exposure, portfolio heat, risk governor (the sole sizing authority). |
| `selector/` | Chooses one proposal among the qualifying signals and journals the others. |
| `execution/` | Order state machine, `service.submit_new_entry` (the only entry send path), safe close, stop modification, reconciliation, UNKNOWN resolution, crash recovery, the non-executing `order_check_probe`. |
| `position_management/` | Continuous expectancy, adaptive exit, re-entry rules, durable state, `review_position_once`. |
| `journal/` | Immutable decision chains and events. |
| `news/` | Keyless calendar providers, cache, blocking windows (fail closed). |
| `simulation/`, `backtest/`, `paper/` | Causal fill model v2, backtest engine, sequential folds, untouched OOS, path stress, PAPER sessions with fingerprinted config. |
| `research/` | Purged K-fold, CPCV, PSR/DSR, PBO, append-only trial ledger. |
| `learning/` | Dataset, training, model walk-forward, training job, registry and lifecycle, promotion gate, Stage-1 observer (zero influence). |
| `rag/` | SQLite RAG memories, TF-IDF index, journal-to-RAG ingestion. |
| `knowledge/` | OKF v0.2 loader, validator, search, advisor and benchmark over the Git-tracked `knowledge/` bundle. |
| `runtime/` | `RuntimeEngine`, scheduler, DEMO and PAPER cycles, market data, news monitor, advisory panel, runtime state, logging, side-effect-free analysis. |
| `dashboard/` | Read-only panels, FastAPI app, WebSocket feed, static page. |
| `cli/` | Operator commands, split by area. |

## Runtime

A single thread runs a priority scheduler, so MT5 calls never overlap.

| Priority | Task | Cadence | Mode |
|---|---|---|---|
| 0 | `position_cycle`: reconcile, resolve UNKNOWN orders, review each position | ~1 s | DEMO |
| 1 | `entry_cycle`: decide once per newly **closed** bar | ~4 s | both |
| 2 | `news_refresh` | ~20 min | both |
| 3 | `rag_ingest` (journal → memories), `cost_evidence_sweep` (DEMO), `rag_rebuild` | 60 s / 60 s / 10 min | both |
| 4 | `heartbeat` → `runtime_state` (read by the dashboard) | ~1 s | both |

Startup fails closed, in this order:
1. Database integrity.
2. Kill switch **read**. It is never bootstrapped or cleared here.
3. MT5 initialize.
4. The account must be DEMO.
5. Resolve and validate the symbols; capture their specs.
6. Assert that the retired strategies are absent.
7. DEMO only: quarantine submissions interrupted by a crash, reconcile, apply UNKNOWN
   resolutions.
8. News refresh.
9. RAG ingest and rebuild.
10. OKF bundle load.

## DEMO entry pipeline

1. Global short-circuit: kill switch, DEMO and permissions, dangerous UNKNOWN, reconciliation,
   news outage, daily loss, drawdown.
2. Features, confirmed regime, six strategies, then `SIGNAL_CREATED`.
3. Advisory evidence (RAG, ML observer, OKF), journaled with **no authority**.
4. The selector, over the live cost estimate. An unknown cost gives `BLOCK_COST`.
5. `calculate_safe_volume`.
6. `submit_new_entry`, which builds fresh evidence and evaluates final permission **twice**,
   calls `order_check`, then sends exactly the checked request.
7. Afterwards: record the position entry context and an execution cost observation.

## PAPER

`run_paper_cycle` uses the same causal cores as the backtest:
- next-bar-open fills and bid/ask triggers;
- stops and targets checked on the entry bar;
- persistent peak R;
- revalidation of deferred entries.

It also applies cross-symbol exposure and correlation, and news windows from the live
calendar. Sessions are fingerprinted, so a config change halts the session instead of mixing
state.

## Data

SQLite (WAL) is authoritative for all runtime truth. The Git-tracked `knowledge/` bundle
(OKF) holds curated documentation only. See `KNOWLEDGE_MEMORY.md`.
