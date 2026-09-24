# Knowledge and memory architecture

AdaptiveScalperNext uses a **hybrid** knowledge architecture: curated knowledge lives in a
Git-tracked **Open Knowledge Format (OKF v0.2)** bundle, and **SQLite** stays authoritative
for everything the running system records. OKF adds to the SQLite trading memory. It does not
replace it.

| | OKF bundle: `knowledge/` | SQLite: `data/*.db` |
|---|---|---|
| Holds | architecture and engineering decisions, strategy definitions, approved research findings, model cards, lessons learned, safety procedures, runbooks | trade records (DEMO and PAPER), journal events, orders, execution incidents, positions, runtime state, kill switch, RAG memories |
| Written by | people and agents, through Git review | the runtime only |
| Authority | advisory documentation; `influence: NONE` | authoritative |
| Consumer | `KnowledgeAdvisor` → `AdvisoryPanel` (journaled evidence), `okf` CLI | everything |

## OKF version

The spec was checked on 2026-09-24. **v0.2 is the latest published OKF specification.** The
canonical home has moved from `GoogleCloudPlatform/knowledge-catalog/okf`, now a frozen
snapshot, to `GoogleCloudPlatform/open-knowledge-format` (`SPEC.md`). The bundle declares
`okf_version: "0.2"` in its root `index.md`.

## What is implemented (`adaptive_scalper/knowledge/`)

| Module | Role |
|---|---|
| `model.py` | `Concept` and `KnowledgeBundle`, the trust tiers derived from `verified` (unverified / machine-confirmed / human-reviewed), lifecycle and timestamp parsing, and the bundle checksum. |
| `loader.py` | Safe loader: `yaml.safe_load` only, 256 KiB per-file cap, symlinks and root escapes refused, non-UTF-8 files reported. It checks OKF §11 conformance (parseable frontmatter, non-empty `type`, reserved `index.md`/`log.md` structure). It never raises on bad content. |
| `validate.py` | Project policy on top of conformance: provenance, verification, lifecycle and supersession checks, symbol and retired-strategy scope, a ban on control keys, and the credential and raw-record scan. A concept with any ERROR is quarantined from every consumer. |
| `search.py` | Direct lookup (id or path), structured filters (type, tag, symbol, strategy) and a weighted keyword search. |
| `advisor.py` | `KnowledgeAdvisor.advise(canonical_symbol, strategy_key)`: approved concepts, and separately the **unverified** evidence concepts, plus stale flags, the bundle checksum and `influence: NONE`. |
| `benchmark.py` | The retrieval benchmark below. |

Project extension keys: `id`, `version`, `supersedes`, `superseded_by`,
`applies_to.{symbols,strategies}`, `strategy_key`, `strategy_version` and `retired`. OKF
requires consumers to tolerate unknown keys.

### Enforced guarantees

These are all tested in `tests/test_knowledge.py`:

- **OKF never controls execution.** The knowledge package imports nothing from gateway,
  execution, core (kill switch or final permission), risk, portfolio, selector,
  position management, learning, runtime or persistence. Only `runtime/engine.py`, which
  hands the advisor to the evidence-only panel, and the operator CLI import it. The
  advisor's output is journaled as `RAG_USED` with `source: OKF` and
  `authority: NONE (advisory evidence only)`. It is never passed to the selector, risk
  governor, final permission or execution service.
- **No risk-limit or kill-switch changes.** Frontmatter keys that look like runtime control
  are validation errors: risk limits, `kill_switch`, `mode`, `enable`/`activate`/`promote`,
  `volume`, `order` and similar.
- **Retired strategies stay retired.** A concept can scope itself to `failed_breakout_fade`
  or `support_resistance_reaction` only if it is `deprecated` **and** `retired: true`. The
  advisor never returns anything for a retired strategy.
- **Unverified research is never promoted.** A Research Finding, Model Card or Lesson
  Learned can only be `stable` once a `human:` actor has verified it. The advisor lists
  evidence concepts without human review under `unverified`, labelled "UNVERIFIED - not
  approved knowledge".
- **No secrets or raw records in Git.** Every file is scanned for:
  - credential assignments (password, API key, token, MT5 login/server), account logins
    and private keys;
  - raw runtime records (broker order/position/deal ids with values, client request ids,
    `payload_json`, CSV/JSONL/SQL dumps).

  A matching file is quarantined, and the test suite fails on the tracked bundle.
- **Failures degrade, never block.** A missing or invalid bundle marks the `okf` component
  DEGRADED in the engine's health. Trading cycles are unaffected.

## RAG memory: journal-to-RAG ingestion

The SQLite RAG memory (`rag_memories`, TF-IDF index) is unchanged in role. It is now fed by
an **ingestion task** instead of by writes from the trading hot path:

- `rag/ingestion.py` reads only the rows newer than each source's watermark
  (`rag_ingestion_state`) and writes typed memories carrying:
  - `source_key` (`journal:<id>`, `paper_trade:<id>`, `incident:<id>`, `trial:<id>`,
    `runtime_event:<id>`);
  - `origin` (`BROKER_DEMO_CONFIRMED`, `PAPER_LIVE_DATA`, `BACKTEST`, `DECISION`,
    `EXECUTION`, `SYSTEM`).
- It is idempotent. A partial unique index on `source_key` (migration 0023) means a
  re-run, or a lost watermark, never duplicates a memory.
- It covers all eight memory types. A rejected proposal is a `REJECTION`, never a
  `TRADE_RESULT`. PAPER and DEMO results are never pooled under one origin.
- The engine runs it as scheduled task `rag_ingest` (priority 3, every 60 s), followed by
  an index rebuild when anything new arrived. A failure marks `rag_ingest` DEGRADED.

## Retrieval benchmark

`adaptive_scalper.knowledge.benchmark.run_benchmark()` compares three retrievers:

- `okf_direct`: `knowledge.search`;
- `tfidf`: the exact `RagIndex` configuration, `TfidfVectorizer(stop_words="english")`
  with cosine similarity;
- `fts5_bm25`: an in-memory FTS5 table using `unicode61`, OR-joined terms and
  `ORDER BY bm25()`.

It runs on two corpora:

- **okf**: the 21 curated concepts, with 18 hand-labelled operator questions.
- **rag**: deterministic synthetic memories in the exact text shape that ingestion writes.
  A result is relevant when it has the same symbol, strategy and decision.

Measured in the cloud container (4 vCPU, Python 3.13, SQLite 3.45.1, scikit-learn 1.9.1),
with k = 3. P@3 is capped below 1.0 whenever a query has fewer than three relevant
documents. Latencies are wall-clock and indicative only.

| corpus | retriever | docs | queries | P@3 | R@3 | MRR | p50 µs | p95 µs | build ms |
|---|---|---|---|---|---|---|---|---|---|
| okf | okf_direct | 21 | 18 | 0.407 | 1.000 | 0.972 | 1358 | 2017 | 41.2 |
| okf | tfidf | 21 | 18 | 0.389 | 0.972 | 0.944 | 1208 | 1617 | 5.1 |
| okf | fts5_bm25 | 21 | 18 | 0.370 | 0.917 | 0.917 | 85 | 119 | 1.3 |
| rag | tfidf | 2000 | 26 | 0.692 | 0.692 | 0.904 | 2335 | 2979 | 21.5 |
| rag | fts5_bm25 | 2000 | 26 | 0.718 | 0.718 | 0.885 | 2572 | 2787 | 7.2 |
| rag | tfidf | 10000 | 26 | 1.000 | 1.000 | 1.000 | 8396 | 10054 | 94.8 |
| rag | fts5_bm25 | 10000 | 26 | 1.000 | 1.000 | 1.000 | 13069 | 15293 | 35.1 |

**Conclusion: keep the TF-IDF RAG index. No retrieval change is justified.**

- On the curated bundle, direct OKF lookup ranks best (MRR 0.97 against 0.94 for TF-IDF and
  0.92 for FTS5). The bundle is small, and structured lookup by id, type, strategy or symbol
  answers most questions exactly. FTS5 is the fastest there, but every retriever is well
  under 2 ms, which is irrelevant against a 4 s entry cadence.
- On RAG-shaped memories, quality is a tie: TF-IDF has the higher MRR, FTS5 the slightly
  higher P@3. TF-IDF also has the lower query latency at 2k and 10k documents. FTS5 builds
  faster, but the rebuild runs off the hot path every 10 minutes.
- Revisit this if the memory store grows well past 100k rows or the advisory panel moves
  onto a latency-critical path. Neither is planned.

Re-run the benchmark with `adaptive-scalper okf benchmark`.

## Adding knowledge

1. Add a `.md` concept under the right folder, with `type`, `title`, `description`,
   `generated {by, at}` and `sources`. Use `status: draft` for anything evidential.
2. Run `adaptive-scalper okf validate`. It must report zero errors.
3. A human reviews it in the pull request. To mark a finding as approved, add
   `verified: [{by: human:<id>, at: <ISO-8601>}]` and set `status: stable`.
4. To replace a concept, add `supersedes: [/old.md]` to the new one, and give the old one
   `status: deprecated` and `superseded_by: /new.md`. Do not delete it.
5. Append a line to `knowledge/log.md`.
