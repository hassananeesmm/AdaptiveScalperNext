# ASN-032 — Physical isolation of the shadow observer (DESIGN NOTE ONLY)

Status: proposal, 2026-10-03. Nothing in this note is implemented. It does not
change the release candidate; it records the target architecture so a later,
separately reviewed change can make the shadow observer's isolation physical
rather than module-level.

## Today

The shadow observer (`shadow/observer.py`) and the counterfactual lifecycle
evaluator (`shadow/lifecycle_counterfactual.py`) run **inside the DEMO runtime
process** (`DemoRuntime._shadow_observe`). Their isolation is proven at module
level:

- a transitive import walk shows neither module can import a gateway,
  execution, broker or order-sending module (tests/test_shadow_observer.py,
  tests/test_shadow_lifecycle.py);
- they receive only data the runtime already holds (closed bars, feature
  vectors, decision-time cost estimates, selector dispositions);
- every exception is caught and journaled; an observer failure never changes a
  decision (test_an_observer_failure_never_changes_a_decision);
- they write only the append-only `shadow_*` tables (migration 0032).

Residual risk: a defect that blocks or slows the shared process (CPU, memory,
SQLite write lock held during a large `resolve_lifecycles` batch) can delay the
trading loop, and the observer shares the runtime's SQLite connection and
memory space.

## Target

```
 trading runtime (DEMO/PAPER)            read-only feed                 shadow process
 ───────────────────────────            ───────────────                 ──────────────
 closes a decision bar  ──► appends one immutable record ──►  reads the feed (never the broker),
 (features, costs, selector  (append-only table or file;     records candidates, resolves
  dispositions, chain_key)    no shadow code in the runtime)  outcomes and lifecycles into its
                                                              OWN database file
```

1. **Trading runtime** writes one append-only `shadow_feed` record per closed
   decision bar: identity fields (mode, symbol, resolution, decision bar,
   strategy key + version, direction), causal features, decision-time cost
   estimate, selector disposition, chain_key. Writing is a single INSERT inside
   the existing bar transaction; no shadow logic runs in the runtime.
2. **Read-only feed.** The shadow process opens the runtime database with
   `mode=ro` (or reads an exported feed file) and never holds a write lock on
   it. It does not open MT5, has no gateway object and no credentials; its
   process has no `ASN_EDGE_CERTIFICATE_PUBLIC_KEY_FILE` and no broker
   configuration.
3. **Isolated shadow process** owns a separate SQLite file
   (`data/shadow.sqlite3`) holding `shadow_candidates`, `shadow_outcomes`,
   `shadow_lifecycle_outcomes` with today's identity constraints and append-only
   triggers. Closed bars come from the read-only feed / bar store, never from
   the broker.
4. **Supervision.** Started and stopped independently of the trading runtime;
   its crash, hang or backlog cannot delay an order decision. Health is shown on
   the dashboard as SHADOW_STALE when the feed is ahead of the processed cursor.

## Acceptance tests for the future change

- the shadow process imports no gateway/execution/broker module (same walk as
  today, run against its entry point);
- it opens the runtime DB read-only (a write attempt fails);
- killing it mid-batch leaves the trading runtime's decisions and timing
  unchanged (fake-clock test);
- identity, idempotence and append-only tests from tests/test_shadow_identity.py
  pass against the new database file;
- candidates and outcomes produced by the separate process equal those of the
  in-process observer on the same recorded feed (equivalence replay).

## Out of scope

No change to the executable path, the certificate trust model, strategies,
selection or exits. Shadow evidence never authorizes exposure; only a signed
certificate from the (not yet existing, ASN-031) offline validation pipeline can.
