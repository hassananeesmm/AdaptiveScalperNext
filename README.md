# Adaptive Scalper Next

An adaptive scalping research and execution platform for **three instruments** (XAUUSD,
GBPJPY, BTCUSD) that runs in **two modes only**:
- **PAPER:** real MetaTrader 5 market data with simulated fills. No order is ever sent.
- **DEMO:** a MetaTrader 5 DEMO account, through one guarded execution path.

> **REAL-MONEY EXECUTION IS DISABLED.** There is no LIVE mode. Every broker-facing path
> refuses a non-DEMO account. The kill switch fails closed and is only bootstrapped or cleared
> by an explicit human operator command.

## Quick start (Windows laptop with MT5)

1. `SETUP.bat`: creates `.venv` (Python 3.13), installs the pinned requirements, migrates the
   database and runs `doctor`.
2. Log the MT5 terminal into your **DEMO** account.
3. `.venv\Scripts\python.exe -m adaptive_scalper.cli symbols`
4. As the operator, bootstrap the kill switch when you decide to allow entries:
   `.venv\Scripts\python.exe -m adaptive_scalper.cli kill-switch bootstrap --operator-id YOU --reason "first start"`
5. `START PAPER.bat` and `START DASHBOARD.bat` (http://127.0.0.1:8765/).
6. Follow **[LOCAL_MT5_HANDOFF.md](LOCAL_MT5_HANDOFF.md)** (steps A–Y) before any DEMO order.

## Launchers

| Launcher | What it does |
|---|---|
| `SETUP.bat` | Creates the venv, installs requirements, migrates the database, runs doctor. Never touches the kill switch. |
| `START PAPER.bat` | PAPER runtime: live data, simulated fills. |
| `START DEMO.bat` | DEMO runtime. Banner: REAL-MONEY EXECUTION: DISABLED / DEMO ACCOUNT REQUIRED / NEW ENTRIES REQUIRE KILL SWITCH DISENGAGED. |
| `START DASHBOARD.bat` | Observer-only dashboard (read-only database, no MT5). |
| `RUN BACKTEST.bat` | Backtest over stored history. |
| `STOP TRADING.bat` | **Engages** the kill switch. New entries stop; positions are *not* closed and keep being managed. |

## Command line

Run `.venv\Scripts\python.exe -m adaptive_scalper.cli --help`. The commands are:

| Area | Commands |
|---|---|
| System | `doctor`, `status`, `health`, `symbols`, `strategies`, `why-no-trade`, `journal recent`, `costs observed` |
| Operator | `kill-switch status`, `engage`, `bootstrap`, `clear` |
| Data | `history bootstrap`, `history status`, `broker-history import`, `broker-history status` |
| Runtime | `paper`, `demo`, `scan`, `analyse`, `reconcile`, `order-check-probe`, `news status`, `news refresh`, `news upcoming` |
| Research | `backtest`, `walk-forward`, `oos`, `path-stress`, `purged-validation` |
| Learning (observer only) | `models`, `learning status`, `learning train`, `learning scores`, `model-walk-forward` |
| Memory and knowledge | `rag status`, `rag stats`, `rag similar`, `rag rebuild-index`, `rag verify-index`, `rag ingest`, `okf status`, `okf validate`, `okf search`, `okf benchmark` |
| UI | `dashboard` |

## Documentation

| Document | Contents |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Modules, data flow, runtime scheduling |
| [docs/SAFETY.md](docs/SAFETY.md) | Every safety guarantee and where it is enforced and tested |
| [docs/RESEARCH_VALIDATION.md](docs/RESEARCH_VALIDATION.md) | Causal simulation, OOS discipline, purged CV, DSR/PBO, model walk-forward |
| [docs/KNOWLEDGE_MEMORY.md](docs/KNOWLEDGE_MEMORY.md) | Hybrid OKF + SQLite knowledge, RAG ingestion, retrieval benchmark |
| [docs/WINDOWS_DEPLOYMENT.md](docs/WINDOWS_DEPLOYMENT.md) | Installing and operating on the laptop |
| [docs/RELEASE.md](docs/RELEASE.md) | Building, verifying and defining a release |
| [docs/QA_REPORT.md](docs/QA_REPORT.md) | Latest cloud QA: tests, static and security scans |
| [LOCAL_MT5_HANDOFF.md](LOCAL_MT5_HANDOFF.md) | The Windows and MT5 steps the cloud cannot perform |

The project status files are `MASTER_BUILD_DIRECTIVE.md` (authoritative),
`PROJECT_STATUS.md`, `WORKLOG.md` and `BUG_BACKLOG.md`.

## Development

```bash
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt   # MetaTrader5 is Windows-only
.venv/bin/python -m pytest -q
```

Tests that need the live terminal (`tests/test_mt5_gateway_live.py`) skip automatically when
MetaTrader5 is unavailable.
