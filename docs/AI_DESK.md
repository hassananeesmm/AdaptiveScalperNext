# AI Trading Desk (advisory research subsystem)

> **ADVISORY ONLY - NO EXECUTION AUTHORITY.** REAL-MONEY EXECUTION IS DISABLED. The desk
> cannot place, modify or close orders, cannot touch the kill switch, risk limits, strategies
> or certificates, and is not in the trading decision path. The trading engine's deterministic
> gates decide everything, exactly as before.

`MASTER_BUILD_DIRECTIVE.md` stays authoritative. Its section 3 forbids a *required* cloud LLM:
the desk runs fully offline in deterministic mode; external models are optional, off by
default, and only add an untrusted narrative. Its Stage-1 "model observer" principle (zero
execution influence) is the desk's operating stage.

## 1. Architectural audit (2026-11-14)

| Topic | Finding |
|---|---|
| Deployed vs development | DEMO runs **0.2.9** (`2dfb171`, `main` = tag v0.2.9, production schema 32). Development stack: `hardening/peak-equity-and-readiness` (488d575) <= `chore/ruff-cleanup` (PR #11) <= `fix/project-drawdown-safety-20261027` (PR #12, f892c5f, migrations 0033+0034). The desk branch `feature/ai-trading-desk` is based on f892c5f; the desk itself needs no trading migration and tolerates schema 32 (missing tables are recorded limitations). |
| Reusable | `persistence.connect_readonly`; causal `features/` (bar features, Wilder ADX, EMA, rolling VWAP); `regimes/classifier`; `core.kill_switch.get_state` (pure SELECT); `config.constants` (allow-lists, hard ceilings); `research.independent.assert_outside_reserved_oos`; the runtime's observer data in `runtime_state` (engine heartbeat, regime, microstructure, risk limits, reconciliation, broker truth, why-no-trade, news, telemetry); DEMO `positions`/`deals`/`position_entry_context`; `execution_cost_observations`; shadow candidates + outcomes; `news_events`; `research_trials`; `entry_decisions`. The `runtime/advisory.py` pattern ("evidence, never authority") is the model. |
| Execution boundaries | Only `execution/service.submit_new_entry` sends entries; `gateway/factory.py` is the only gateway construction site; `OperatorAuthority` only in `cli/operator.py`. The desk imports none of them (AST-audited). |
| Scheduler | The runtime is a single-threaded priority scheduler (position cycle ~1 s). A model call inside it could delay position management, so the desk runs in its **own process** and the runtime never imports it (AST-audited). |
| Config constraint | Adding a section to the trading `AppConfig` would change the PAPER session fingerprint; the desk has its own `config/ai_desk.toml`. |
| Data constraint | The `bars` table ends 2026-09-18 (the runtime does not persist live bars) and those last months are the reserved untouched OOS. Fresh market state exists only in `runtime_state`; forward checks become UNSCORABLE when later bars are not stored (reported, not hidden). |
| Missing before | No multi-agent layer, no research journal for AI analyses, no evaluation of whether such analysis helps. |

## 2. Architecture

```text
TRADING DB (read-only, connect_readonly)        config/ai_desk.toml (disabled by default)
          |                                                 |
   EventBus.poll (fixed SQL)  --> new events --> DeskService (separate process, `ai-desk serve`)
          |                                                 |
   SnapshotBuilder (as-of bounded, immutable, content-hashed evidence)
          |
   Market x3 -> News -> Risk -> Strategy -> Learning -> Skeptic -> Coordinator -> Skeptic review
          |                     (each in a time-bounded worker thread)
   contracts.validate_result (schema, evidence ids, no execution-shaped keys)
          |
   optional ProviderRouter.narrate (budget, breaker, cache, strict JSON, UNTRUSTED)
          |
   Journal: data/ai_desk.sqlite3 (own migrations, append-only results, idempotent keys)
          |                 |                       |
   dashboard /api/ai-desk   ai-desk report/health   evaluator (forward, replay, ablation)

   NO PATH TO: gateway, execution, kill switch writes, risk governor, selector, certificates
```

Package `adaptive_scalper/ai_desk/`: `configuration`, `contracts`, `security`,
`snapshot_builder`, `metrics`, `agents/` (base + seven agents), `providers/` (`base`:
router + deterministic; `http`: Anthropic, OpenAI-compatible incl. Grok and local
endpoints), `orchestration`, `event_bus`, `scheduler`, `journal` (+ `migrations/`),
`evaluator`, `health`, `dashboard_panels`. CLI: `adaptive_scalper/cli/ai_desk.py`.

## 3. The seven agents

All agents are deterministic Python over one immutable snapshot. Outputs are
`AgentResult` objects (schema v1, `extra="forbid"`) with evidence ids, counterarguments,
limitations, typed claims, and a confidence only when a basis is stated (heuristic, scored
for calibration later).

| Agent | Evidence | Produces |
|---|---|---|
| Market Analyst (per symbol) | closed bars (M5 300, M1 30), runtime regime + microstructure, previous desk assessment | condition (TRENDING / RANGING / COMPRESSION / BREAKOUT / HIGH_VOLATILITY / UNCERTAIN), trend, ADX(14), EMA20/50, VWAP +/-2 sigma, volatility percentile, indicator conflicts, freshness, why it changed, a forward-checkable volatility statement. STALE when no fresh data. Never a direction forecast. |
| Strategy Analyst | DEMO trades (net of commission/swap/fees, in R), shadow outcomes, research trials, strategy config | per strategy x symbol: win rate, expectancy + 95 % bootstrap CI, PF, drawdown, cost share, by-regime fit with today's regime, theory-vs-realized gap, repeated underperformers (CI < 0), overfitting signals (negative purged CV), UNTESTED hypotheses. Retired strategies: history only. |
| News Analyst | stored calendar rows (keyless providers), provider health, engine news decision | facts (title, currency, impact, scheduled/retrieved times, revision) separated from labelled interpretation; currency -> instrument mapping; imminent (<= 60 min) high-impact events; conflicts/revisions; measured range reaction where bars cover past events. The engine blackout is authoritative. |
| Risk Analyst | telemetry (identity stripped), kill switch (read), limits (runtime vs config vs hard ceilings), persisted peak, incidents, reconciliation, broker truth, costs, recent proposals | warnings with severity; drawdown and daily-loss recomputation; spread vs cap and observed p95; cost evidence depth/provenance; slippage; missing stops; correlated USD exposure; non-DEMO account observed. Differences with the governor are AUDIT FINDINGS. |
| Skeptic | every other result (Coordinator included) | per claim SUPPORTED / WEAK_EVIDENCE / CONTRADICTED / INSUFFICIENT_DATA / ABSTAIN with objections: look-ahead, missing/stale evidence, small samples (< 100), CIs spanning zero, unverified costs, theoretical outcomes, untested forecasts (< 30 scored), indicator conflicts, cross-agent contradictions, shared evidence counted once, overconfidence. "Agreement is not evidence of an edge." |
| Learning Analyst | trades, shadow candidates/outcomes, decisions, costs, the desk's own scored forecasts | blocked-opportunity outcomes by block reason (possible false rejections = research items, never "relax the gate"), losses by session/regime, cost-dominated losers, deterioration (recent vs earlier, diff CI), feature drift (PSI), execution-cost issues; recommendations all "require separate validation". |
| Coordinator | all results | completeness/freshness, INCOMPLETE ASSESSMENT when critical evidence is missing, disagreements preserved verbatim, supported vs unsupported claims, justified read-only follow-ups (run once), provider availability/latency/cost, the advisory summary. No direction/size/entry field exists. The Skeptic then reviews the summary and may disagree. |

## 4. Safety and security

Proven by `tests/test_ai_desk_safety.py` (AST audits run on every test run) and
`tests/test_ai_desk.py`:

- No desk module imports `gateway` (except the plain `Bar` dataclass), `execution`, `runtime`,
  `position_management`, `selector`, `validation`, `risk.governor`, `core.final_permission`,
  `core.operator_authority`, `core.peak_equity_reset`, `cli.operator`, `MetaTrader5` or
  `subprocess`; none calls `order_send`, `order_check`, `engage`, `bootstrap`, `clear`,
  `eval`, `exec`, `system`, `popen`, certificate issuance or `executescript`.
- Only `journal.py` opens a writable database (its own); the trading DB is opened with
  `connect_readonly` only, and a write through it fails (tested). The kill switch is only read.
- No trading package imports `ai_desk`; a full cycle (with a failing provider and a prompt
  injection in a news title) leaves the trading DB dump byte-identical; the hard ceilings
  and `ALLOWED_MODES` are unchanged.
- Contracts reject unknown evidence ids, extra fields, execution-shaped keys anywhere in
  findings, and confidences without a basis.
- Untrusted text (news titles, model output) is sanitized (instruction-like phrases removed,
  bounded, secrets redacted); prompts mark data as data; model responses must be strict JSON
  with resolvable citations and no trading/control instruction, else rejected.
- Secrets: only environment-variable NAMES in config; keys are read at call time, never logged
  or printed (`doctor` prints presence only); errors are redacted. `config/ai_desk.toml` is
  git-ignored.
- Model-generated text is never executed (no shell, SQL or eval); all SQL is fixed in code.

## 5. Configuration, providers and cost control

Copy `config/ai_desk.example.toml` to `config/ai_desk.toml`. Defaults: `enabled = false`,
`provider = "deterministic"`, `allowed_external_data = []`, budgets 0.

An external call happens only when **all** hold: a non-deterministic provider is in the chain,
the agent is in `llm_agents`, its data category (`market_features`, `strategy_statistics`,
`news_calendar`, `risk_summary`) is in `allowed_external_data`, the daily token budget (and,
for priced providers, the USD budget) has room for the pre-flight estimate, and the provider's
circuit is closed. Each call has a timeout, bounded retries with exponential backoff, a
per-provider minimum interval and concurrency limit, a response cache (`cache_ttl_seconds`),
and falls through `fallback_providers` to `deterministic` (no narrative). Every attempt is
journaled in `provider_calls` (status, latency, tokens, cost).

Hardening (verified 2026-11-14 against a local stub HTTP server, `tests/test_ai_desk_providers.py`):
HTTP 429/5xx are retried and a numeric `Retry-After` is honoured (capped at 60 s); 4xx is not
retried; a half-open circuit re-opens on its first failure; a rejected (invalid / instruction-like)
model answer counts as a breaker failure; response bodies over 1 MB, malformed or wrongly shaped
bodies, dropped connections and adapter bugs end as a FAILED narrative, never a failed cycle;
`narrative_budget_seconds` (default 120) caps the wall-clock time of all model calls in one cycle,
and no retry starts if it would cross that deadline. The key is sent only as a header and never
appears in the journal or the prompt. A consumer chat subscription
does not provide API credits; API keys are separate and billed by the provider.

| kind | endpoint | key |
|---|---|---|
| `deterministic` | none | none |
| `anthropic` | Anthropic Messages API | `api_key_env`, e.g. `ANTHROPIC_API_KEY` |
| `grok` | `https://api.x.ai/v1/chat/completions` | e.g. `XAI_API_KEY` |
| `openai_compatible` | `base_url` + `/chat/completions` (OpenAI-compatible hosts, local Ollama / LM Studio) | optional |

The laptop (16 GB RAM, no NVIDIA GPU) runs the deterministic desk comfortably; a local CPU
model is possible but slow - keep `timeout_seconds` generous and `llm_agents` small.

## 6. Autonomous operation

`ai-desk serve` (or `START AI DESK.bat`) polls every `poll_seconds`: events are detected from
data the runtime already writes (closed-bar times, new shadow candidates, closed positions,
calendar changes, a hash of kill switch / reconciliation / broker truth / engine block /
open-position count, and an hourly scheduled report). Duplicates are dropped by event key;
agents run only when their minimum interval (`[intervals]`) has passed; a burst becomes one
cycle. Forward checks are scored hourly; retention pruning runs daily. Crashed runs are
marked ABANDONED at the next start; committed results survive. If the desk stalls or dies,
nothing in the trading runtime depends on it.

### Market-data freshness

Root cause of STALE market analysis (measured 2026-11-14 on the laptop's trading DB):
1. The trading runtime has not run since 2026-10-28 (heartbeat ~16.7 days old), so
   `runtime_state` (microstructure observer, regime, quotes) is last-known.
2. Independently of (1), the runtime never writes the `bars` table: it keeps the bars it fetches
   in memory. `bars` is filled only by the operator command `history bootstrap`; the newest stored
   M5 bars are ~56 days old. So bar-based statistics (volatility percentile, VWAP band, forward
   checks) stay stale even while the runtime runs.

What the desk does (never opens MT5, never starts the runtime, never touches the kill switch):
each Market Analyst result carries `data_freshness.state` = `FRESH_BARS`,
`RUNTIME_OBSERVER_ONLY` (runtime live; bars stale -> condition from the runtime observer, no
bar statistics), `RUNTIME_LIVE_NO_OBSERVER` or `RUNTIME_DOWN`, the bar / observer / quote ages
and a remediation line. When everything is stale it describes the NEWEST source and cites only
that source (fixed 2026-11-14: it previously preferred 56-day-old bars over the 16-day-old
observer). `ai-desk doctor` prints the heartbeat and bar ages (informational).

Safe ways to restore freshness (operator decisions, in order of preference):
- Run the trading runtime through the normal procedure; the desk then reads the fresh observer
  and quotes (`RUNTIME_OBSERVER_ONLY`).
- PROPOSAL, not implemented (it changes the trading runtime, so it needs its own branch, review
  and operator approval): a low-priority runtime task that stores the closed bars the runtime
  already fetched through the existing idempotent `history.store.insert_bars`. One MT5 client
  stays the only MT5 client; no new broker call; entries and gates unchanged.
- Interim, only while the runtime is STOPPED: the existing `history bootstrap` command refreshes
  `bars` (it is a broker-facing CLI command and must not run concurrently with the runtime).
  Bars after 2026-07-01 fall in the reserved OOS for research purposes; the desk's
  CURRENT-mode description may read them, its evaluator replay refuses them.

### Performance (measured 2026-11-14, laptop, 16 logical CPUs, production DB read-only)

Reproduce with `.venv\Scripts\python scripts\ai_desk_benchmark.py --cycles 5` (read-only on the
trading DB, throw-away journal). Two runs of 5 cycles while nothing else
was running: cycle median 2.4-2.5 s (min 1.8 s, max 2.9 s); snapshot build 0.1-0.2 s; Strategy and
Learning Analyst ~1.0-1.1 s each (bootstrap CIs); all other agents < 40 ms; process peak working set
~108 MB; Python-traced allocation per cycle ~1.6 MB. CLI wall time `ai-desk run-once` 8-9.5 s, of
which ~3.8 s is importing the full CLI surface. The earlier "16 s" figure was the same cycle
measured under `tracemalloc` (which slows these pure-Python bootstraps ~4x: 7.6-13.8 s reproduced)
while the full test suite was running; it is not the production latency. `ai-desk evaluate`:
34-39 s.

## 7. Operator runbook

1. `copy config\ai_desk.example.toml config\ai_desk.toml` (optional; defaults are safe).
2. `.venv\Scripts\python -m adaptive_scalper.cli ai-desk doctor` - must print `"ok": true`.
3. One manual analysis (works while disabled):
   `.venv\Scripts\python -m adaptive_scalper.cli ai-desk run-once` then `ai-desk report`.
4. Evaluation: `ai-desk evaluate` (default replay = the 60 days before the reserved OOS).
5. Autonomous service: set `enabled = true`, then double-click `START AI DESK.bat`
   (runs `doctor`; stops if it fails; then `serve`; Ctrl+C stops it; trading is unaffected).
6. Dashboard: `START DASHBOARD.bat` -> view **AI Trading Desk** (GET `/api/ai-desk`, read-only).
7. Health: `ai-desk status`, `ai-desk agents`, `ai-desk health`.
8. To use a model: add a `[[providers]]` block, set `provider`/`fallback_providers`,
   `allowed_external_data` and a positive budget, set the key in the environment (never in a
   file in Git), restart the service.

Deployment to the operational laptop requires explicit operator approval (merge, then the
normal release procedure). The desk needs no trading-DB migration; it creates
`data/ai_desk.sqlite3` on first use.

## 8. Evaluation - does it help?

Pre-registered (`evaluator.PROTOCOL_VERSION = ai-desk-eval/1`, hashed and journaled with a
trial number; repeated looks at the same data are counted as extra trials):

- Filters derived from the agents' published logic, never fitted: Market rejects
  ERRATIC / VOLATILITY_EXPANSION / UNKNOWN regimes; Risk rejects movement-to-cost < 2, spread
  percentile >= 0.9, high-impact news within 30 min or a non-ALLOW news status; Skeptic rejects a
  strategy x symbol whose realized expectancy CI (trades **closed before** the decision, >= 30)
  lies below zero.
- Ablations: baseline, +Market, +Market+Risk, +Skeptic, full desk - on (a) realized DEMO trades
  with broker costs and (b) shadow candidates (theoretical net R at 30 min, estimated costs).
- Acceptance: >= 100 kept and >= 30 rejected, the 95 % paired-bootstrap CI of
  (kept - baseline expectancy) above zero, and kept drawdown <= baseline. Anything else is
  "NOT DEMONSTRATED" or "INSUFFICIENT SAMPLE".
- Market Analyst replay: genuine as-of reconstruction (deterministic agent, only bars at or
  before each decision) outside the reserved OOS, scored against the majority-class baseline.
  LLM narratives are never replayed historically (a present-day model knows the future); any
  such study would be labelled RETROSPECTIVE.

**Result on the production data (read-only, 2026-11-13 UTC, trial #1):**

| Dataset | Config | Kept n | Kept expectancy (R) | Improvement 95 % CI | Verdict |
|---|---|---|---|---|---|
| DEMO (506 trades) | baseline | 506 | -0.038 (CI -0.087..+0.015) | - | baseline |
| DEMO | +Market | 503 | -0.039 | -0.007..+0.004 | insufficient sample (3 rejected) |
| DEMO | +Market+Risk | 157 | -0.015 | -0.054..+0.100 | **not demonstrated** |
| DEMO | +Skeptic | 506 | -0.038 | 0..0 | insufficient sample (0 rejected) |
| DEMO | full desk | 157 | -0.015 | -0.054..+0.100 | **not demonstrated** |
| SHADOW (180) | baseline | 180 | -0.255 (CI -0.499..-0.009) | - | baseline |
| SHADOW | +Market+Risk / full | 19 / 10 | -0.023 / -0.173 | spans zero | insufficient sample |

Market Analyst volatility statements (as-of replay, 60 days before the OOS): hit rate 0.51
(XAUUSD, n=912), 0.57 (BTCUSD, n=1418), 0.51 (GBPJPY, n=1008) versus a majority-class
baseline of about 0.35 - this is ordinary volatility clustering, **descriptive skill, not a
trading edge**.

**Conclusion: the AI desk has not demonstrated an improvement over the baseline.** No desk
output is, or can become, permission to trade; the authoritative path to any edge claim remains
the preregistered forward-evidence protocol and the signed certificate (ASN-031).

## 9. Limitations and blocked items

- Live market analysis needs the runtime to be running (fresh `runtime_state`); stored bars are
  stale (end 2026-09-18) because the runtime does not persist bars (see "Market-data
  freshness"). With the runtime stopped the desk correctly reports STALE / INCOMPLETE.
- Forward checks need later bars in the trading DB; the runtime does not persist live bars, so
  live forecasts end UNSCORABLE after two days unless bars are imported (`history` commands).
- External providers are implemented and tested with fakes; **no real API call was made**
  (no key configured). BLOCKED ON OPERATOR CREDENTIALS + BUDGET DECISION.
- `START AI DESK.bat`: its two commands were run on the laptop (doctor ok, serve refuses while
  disabled) and it now stops if doctor fails; double-clicking it in Explorer remains a manual
  step. Dashboard view: verified 2026-11-14 in Chromium (Playwright) against the production DB
  (read-only) and a real desk journal at 1366x768 and 911x512: five panels render, no page-level
  horizontal scroll, POST/PUT/DELETE on `/api/ai-desk` return 405.
- Statistical power is low: few trades per strategy x regime; most claims are WEAK_EVIDENCE by
  design.
