---
type: Architecture Decision
id: adr/hybrid-knowledge
title: Hybrid knowledge architecture (OKF for curated knowledge, SQLite for runtime truth)
description: Curated knowledge lives in a Git-tracked OKF bundle; every runtime record stays in SQLite, and the RAG memory is derived from SQLite.
tags: [architecture, knowledge, okf, rag, sqlite]
version: 1
status: stable
generated:
  by: claude-code/1
  at: 2026-09-24T00:00:00Z
sources:
  - id: okf-spec
    resource: https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/main/SPEC.md
    title: Open Knowledge Format v0.2 specification
  - id: knowledge-package
    resource: repo:adaptive_scalper/knowledge/__init__.py
---
# Decision

Two stores with separate responsibilities:

| Store | Holds | Authority |
|---|---|---|
| OKF bundle (`knowledge/`, Git) | architecture and engineering decisions, strategy definitions, approved research findings, model cards, lessons learned, safety procedures, runbooks | advisory documentation |
| SQLite (`data/*.db`) | trade records, journal events, execution incidents, positions, orders, runtime state, kill switch, RAG memories | authoritative |

The RAG memory stays in SQLite. It is derived by the journal-to-RAG ingestion task from
authoritative rows and indexed with TF-IDF. OKF does not replace it.

# Consequences

- OKF content is reviewed like code. It carries provenance (`sources`, `generated`),
  verification (`verified`, which gives the trust tier) and lifecycle (`status`,
  `stale_after`, `supersedes`) metadata.
- The knowledge advisor only produces journaled evidence with `influence: NONE`. It cannot
  place orders, change risk limits, touch the kill switch, re-activate retired strategies
  or promote research. The validator and an import audit enforce this.
- Credentials, private broker data and raw runtime records never enter the bundle. The
  validator scans every file.
