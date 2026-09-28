# Safety guarantees

Every guarantee below is enforced in code **and** covered by an automated test. Tests marked
*audit* walk the real source ASTs on every run, so a future change that breaks a rule fails
the suite even when no behavioural test touches that code.

> **REAL-MONEY EXECUTION IS DISABLED.**

| # | Guarantee | Enforced in | Proven by |
|---|---|---|---|
| 1 | Only PAPER and DEMO exist; there is no LIVE or REAL mode anywhere in the source | `config/constants.py` `ALLOWED_MODES` | `test_runtime_architecture.py::test_there_is_no_live_or_real_execution_mode` (audit), `test_config.py` |
| 2 | A REAL or CONTEST account is refused, in PAPER as well as DEMO | `runtime/engine.py` startup, `gateway/demo_gate.py` before every mutation, `cli/common.open_gateway` | `test_runtime.py` (startup refusal, account switched to REAL mid-run), `test_demo_gate.py`, `test_order_check_probe.py` |
| 3 | Only XAUUSD, GBPJPY and BTCUSD are executable; an unresolved symbol is excluded, never substituted | `constants.ALLOWED_CANONICAL_SYMBOLS`, symbol resolver and validation | `test_symbol_resolver.py`, `test_symbol_validation.py`, `test_runtime.py` |
| 4 | `failed_breakout_fade` and `support_resistance_reaction` stay retired: never registered, trained, promoted, advised or re-activated by knowledge | `RETIRED_STRATEGY_KEYS`, `StrategyRegistry.register`, engine startup, training job, OKF validator | `test_strategy_registry.py`, `test_learning_jobs.py`, `test_knowledge.py` |
| 5 | Hard risk ceilings: 0.25 % per trade, 0.75 % total open + pending + proposed, 2 % daily loss, 5 % drawdown, 2 positions maximum, 1 per symbol -- configuration may lower them, never raise them; sizing rounds down, never up | `config/constants.HARD_RISK_CEILINGS` enforced by `config/loader.RiskConfig` and `risk/governor.RiskLimits`, `risk/governor.py`, `portfolio/exposure.py`, `core/final_permission.py`; the same halts are applied historically in backtest and PAPER | `test_config.py` (ceiling tests), `test_risk_governor.py`, `test_portfolio_exposure.py`, `test_final_permission.py`, `test_simulation_phase0.py` |
| 6 | The kill switch fails closed: UNINITIALIZED and INVALID block new entries, and only DISENGAGED permits exposure | `core/kill_switch.py` | `test_kill_switch.py` |
| 7 | Bootstrap and clear are **explicit human operator commands only**; `OperatorAuthority` is constructed only in `cli/operator.py`; startup, runtime, launchers, ML, RAG and OKF never call them | `cli/operator.py` | `test_runtime_architecture.py` (audits), `test_runtime.py::test_startup_never_bootstraps_or_clears_the_kill_switch`, `test_launchers.py` |
| 8 | STOP TRADING engages the kill switch and does **not** close positions; managing risk on existing positions continues | `STOP TRADING.bat`, `cli/operator.cmd_engage`, the runtime keeps running `position_cycle` | `test_launchers.py`, `test_runtime.py` (kill switch engaged mid-run keeps managing) |
| 9 | One gateway construction site, always synchronized | `gateway/factory.py` | `test_runtime_architecture.py` (audit) |
| 10 | Only `execution/service.py` sends new entries; `order_send` / `order_check` are called only by the three execution services, plus the `order_check` probe, which has no send path | `execution/` | `test_runtime_architecture.py`, `test_architecture_execution_boundary.py` (audits), `test_order_check_probe.py` |
| 11 | Final permission is evaluated from fresh broker and database truth **twice** per entry, and only the exact checked request is sent | `execution/service.submit_new_entry`, `runtime/demo.fresh_evidence` | `test_execution_service.py`, `test_runtime.py` (two `ENTRY_ALLOWED` per chain) |
| 12 | Dangerous UNKNOWN orders block all new exposure. An `order_send` exception is UNKNOWN, never "not sent". A crash mid-submission is quarantined at startup. Resolution requires positive broker proof. | `execution/service.py`, `execution/recovery.py`, `execution/unknown.py` | `test_broker_chaos.py` (38 deterministic fault-injection tests), `test_execution_unknown.py`, `test_runtime.py`, `test_runtime_restart.py` |
| 13 | Reconciliation must be clean before new exposure; it repairs local state only and never sends. The single, time-bounded exception (ASN-022): the broker's own SL/TP execution order of one of our open positions (market type, our position, opposite side, our magic, `[sl`/`[tp` comment, <= 30 s old) is informational, never a block; anything else unknown still blocks | `execution/reconciliation.py` | `test_execution_reconciliation.py`, `test_asn022_protective_close.py`, `test_cli_commands.py` |
| 14 | News outage, staleness or provider conflict blocks new entries; open positions are still managed | `news/`, `runtime/news_monitor.py` | `test_news_blocking.py`, `test_runtime.py` |
| 15 | Unknown costs are never treated as zero in DEMO (`BLOCK_COST`); simulated results with unmeasured costs are labelled `UNVERIFIED_ASSUMPTION` | `runtime/demo.live_cost_estimate`, `simulation/fill_model.py` | `test_runtime.py`, `test_simulation_phase0.py` |
| 16 | ML, RAG and OKF are evidence only: they cannot increase risk, clear the kill switch, authorize execution or promote research. The ML observer is Stage 1 with zero influence; training registers BASELINE at most and only *reports* the promotion gate. | `runtime/advisory.py`, `learning/observer.py`, `learning/jobs.py`, `knowledge/validate.py` | `test_runtime.py` (identical decisions with every advisory source raising), `test_learning_structural_safety.py`, `test_learning_jobs.py`, `test_knowledge.py` (isolation audits), `test_rag_service.py` |
| 17 | An advisory or observability failure (RAG, OKF, ingestion, cost evidence, dashboard) degrades health, never trading | the engine's guarded tasks, the dashboard in a separate process | `test_rag_ingestion.py`, `test_knowledge.py`, `test_cost_observations.py`, `test_dashboard_panels.py` |
| 18 | The dashboard is an observer: read-only SQLite, no MT5, GET-only endpoints, 127.0.0.1 | `dashboard/app.py`, `persistence.connect_readonly`, `cli/dashboard.py` | `test_dashboard_panels.py` |
| 19 | Untouched OOS stays untouched: overlap with design data, or reuse, is refused; training never reads OOS runs or reserved ranges | `backtest/oos.py`, `learning/jobs.py` | `test_backtest_oos.py`, `test_cli_commands.py`, `test_learning_jobs.py` |
| 20 | PAPER sessions cannot silently mix configurations (fingerprint) | `paper/engine.py` | `test_paper_engine.py`, `test_simulation_phase0.py`, `test_runtime.py` |
| 21 | No secrets in Git: credentials live only in the MT5 terminal; `.gitignore` covers databases, logs, keys and `.env`; OKF files are scanned; the release is built from tracked files with a forbidden-file check | `.gitignore`, `knowledge/validate.scan_text`, `scripts/build_release.ps1` | `test_knowledge.py`, `test_launchers.py`, QA secret scan (`docs/QA_REPORT.md`) |

## What the cloud did not verify

The cloud never connected to MT5. Live-terminal behaviour is BLOCKED-ON-LOCAL-MT5; see
`LOCAL_MT5_HANDOFF.md` ("Items the cloud could not verify").
