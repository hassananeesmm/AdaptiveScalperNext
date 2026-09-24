"""Retrieval benchmark: direct OKF lookup vs the RAG TF-IDF index vs
SQLite FTS5/BM25 (completion directive Phase 4/5: measure before changing
retrieval).

Two corpora, each with labelled queries:

- `okf`: the curated knowledge bundle; hand-labelled questions an operator
  or the advisor actually asks, each with the concept ids that answer it.
- `rag`: deterministic synthetic memories in exactly the text shape
  `rag.ingestion` writes (symbol, strategy, direction, regime, decision,
  reason), with relevance = same symbol + strategy + decision -- the
  "similar past situations" question the advisory panel asks.

Retrievers: `okf_direct` (knowledge.search, bundle only), `tfidf` (the
same TfidfVectorizer configuration as `rag.index.RagIndex`) and
`fts5_bm25` (an in-memory FTS5 table ranked by bm25()). Metrics per
retriever: precision@k, recall@k, MRR, per-query latency p50/p95 and index
build time. Deterministic apart from wall-clock latency.
"""

from __future__ import annotations

import random
import re
import sqlite3
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from adaptive_scalper.knowledge.search import search as okf_search
from adaptive_scalper.knowledge.validate import validate_bundle

K = 3

OKF_QUERIES: tuple[tuple[str, frozenset[str]], ...] = (
    ("what happens when order_send raises an exception", frozenset({"lesson/order-send-exception-is-unknown",
                                                                     "runbook/unknown-order"})),
    ("unknown order outcome reconciliation blocked entries", frozenset({"runbook/unknown-order",
                                                                         "lesson/order-send-exception-is-unknown"})),
    ("how do I clear or bootstrap the kill switch", frozenset({"safety/kill-switch", "runbook/first-start"})),
    ("stop trading emergency close positions", frozenset({"safety/stop-trading"})),
    ("risk per trade daily loss drawdown limits", frozenset({"safety/risk-limits"})),
    ("is live real money trading allowed", frozenset({"safety/demo-only"})),
    ("breakout regime strategy", frozenset({"strategy/range_breakout"})),
    ("fade extreme within range compression reversion", frozenset({"strategy/statistical_reversion"})),
    ("wick rejection volatility expansion", frozenset({"strategy/volatility_expansion"})),
    ("velocity acceleration short horizon", frozenset({"strategy/microstructure_acceleration"})),
    ("pullback counter move in trend", frozenset({"strategy/pullback_continuation"})),
    ("ride confirmed trend efficiency ratio", frozenset({"strategy/momentum_continuation"})),
    ("is there a validated out of sample edge", frozenset({"research/no-validated-edge-yet"})),
    ("ml observer influence promotion", frozenset({"model/entry-observer"})),
    ("entry bar stop target simulation", frozenset({"lesson/entry-bar-stops-and-targets", "adr/causal-fill-model"})),
    ("where are trade records stored sqlite okf", frozenset({"adr/hybrid-knowledge"})),
    ("who may call order_send", frozenset({"adr/single-execution-path"})),
    ("first start windows paper demo", frozenset({"runbook/first-start"})),
)

_SYMBOLS = ("XAUUSD", "GBPJPY", "BTCUSD")
_STRATEGIES = ("momentum_continuation", "pullback_continuation", "range_breakout", "statistical_reversion",
               "volatility_expansion", "microstructure_acceleration")
_DECISIONS = {
    "BLOCK_NEWS": "high impact news window",
    "BLOCK_RISK_TOTAL": "total open risk above limit",
    "BLOCK_COST_EDGE": "expected edge below cost",
    "BLOCK_CORRELATION": "correlated exposure too high",
    "BLOCK_REENTRY_CHURN": "re-entry too soon after exit",
    "CLOSED_TARGET": "target reached",
    "CLOSED_STOP": "protective stop hit",
}
_REGIMES = ("TRENDING_UP", "TRENDING_DOWN", "RANGE", "BREAKOUT", "VOLATILITY_EXPANSION", "COMPRESSION")


@dataclass(frozen=True)
class Document:
    doc_id: str
    text: str


@dataclass(frozen=True)
class RetrieverResult:
    corpus: str
    retriever: str
    documents: int
    queries: int
    precision_at_k: float
    recall_at_k: float
    mrr: float
    latency_p50_us: float
    latency_p95_us: float
    build_ms: float


def rag_corpus(n: int = 2000, seed: int = 7) -> tuple[list[Document], list[tuple[str, frozenset[str]]]]:
    rng = random.Random(seed)
    docs, groups = [], {}
    for i in range(n):
        symbol, strategy = rng.choice(_SYMBOLS), rng.choice(_STRATEGIES)
        decision, reason = rng.choice(list(_DECISIONS.items()))
        direction, regime = rng.choice(("BUY", "SELL")), rng.choice(_REGIMES)
        text = f"{symbol} {strategy} {direction} regime {regime} rejected {decision}: {reason}"
        if decision.startswith("CLOSED"):
            text = f"{symbol} {strategy} {direction} regime {regime} -> {decision} R {rng.uniform(-1, 2):.2f}: {reason}"
        doc_id = f"m{i}"
        docs.append(Document(doc_id, text))
        groups.setdefault((symbol, strategy, decision), set()).add(doc_id)
    queries = []
    for (symbol, strategy, decision), ids in sorted(groups.items())[::5]:
        queries.append((f"{symbol} {strategy} BUY regime RANGE {decision} {_DECISIONS[decision]}", frozenset(ids)))
    return docs, queries


def _tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9_]{2,}", text)


class TfidfRetriever:
    name = "tfidf"

    def __init__(self, docs: list[Document]) -> None:
        self.docs = docs
        self.vectorizer = TfidfVectorizer(stop_words="english")   # == rag.index.RagIndex
        self.matrix = self.vectorizer.fit_transform([d.text for d in docs])

    def query(self, text: str, k: int) -> list[str]:
        scores = cosine_similarity(self.vectorizer.transform([text]), self.matrix)[0]
        ranked = sorted(range(len(self.docs)), key=lambda i: scores[i], reverse=True)
        return [self.docs[i].doc_id for i in ranked[:k] if scores[i] > 0]


class Fts5Retriever:
    name = "fts5_bm25"

    def __init__(self, docs: list[Document]) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE VIRTUAL TABLE docs USING fts5(doc_id UNINDEXED, body, tokenize='unicode61')")
        self.conn.executemany("INSERT INTO docs (doc_id, body) VALUES (?, ?)", [(d.doc_id, d.text) for d in docs])

    def query(self, text: str, k: int) -> list[str]:
        terms = {t.lower() for t in _tokens(text)}
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in sorted(terms))
        rows = self.conn.execute("SELECT doc_id FROM docs WHERE docs MATCH ? ORDER BY bm25(docs) LIMIT ?", (match, k))
        return [r[0] for r in rows]


def _evaluate(corpus: str, name: str, docs_count: int, build_ms: float, run: Callable[[str, int], list[str]],
              queries) -> RetrieverResult:
    precisions, recalls, rr, latencies = [], [], [], []
    for text, relevant in queries:
        start = time.perf_counter()
        got = run(text, K)
        latencies.append((time.perf_counter() - start) * 1e6)
        hits = [d for d in got if d in relevant]
        precisions.append(len(hits) / K)
        recalls.append(len(hits) / min(len(relevant), K))
        rr.append(next((1.0 / (i + 1) for i, d in enumerate(got) if d in relevant), 0.0))
    latencies.sort()
    p95 = latencies[min(len(latencies) - 1, int(round(0.95 * (len(latencies) - 1))))]
    return RetrieverResult(corpus, name, docs_count, len(queries), round(statistics.fmean(precisions), 3),
                           round(statistics.fmean(recalls), 3), round(statistics.fmean(rr), 3),
                           round(statistics.median(latencies), 1), round(p95, 1), round(build_ms, 2))


def _timed(factory):
    start = time.perf_counter()
    obj = factory()
    return obj, (time.perf_counter() - start) * 1e3


def run_benchmark(bundle_root: str | Path, *, rag_documents: int = 2000) -> list[RetrieverResult]:
    results = []

    bundle, build = _timed(lambda: validate_bundle(bundle_root))
    concepts = [c for c in bundle.concepts.values() if not bundle.errors_for(c.path)]
    okf_docs = [Document(c.id, c.search_text()) for c in concepts]
    results.append(_evaluate("okf", "okf_direct", len(okf_docs), build,
                             lambda q, k: [h.concept.id for h in okf_search(bundle, q, top_k=k, include_deprecated=True)],
                             OKF_QUERIES))
    for cls in (TfidfRetriever, Fts5Retriever):
        retriever, build = _timed(lambda: cls(okf_docs))
        results.append(_evaluate("okf", cls.name, len(okf_docs), build, retriever.query, OKF_QUERIES))

    docs, queries = rag_corpus(rag_documents)
    for cls in (TfidfRetriever, Fts5Retriever):
        retriever, build = _timed(lambda: cls(docs))
        results.append(_evaluate("rag", cls.name, len(docs), build, retriever.query, queries))
    return results


def format_table(results: list[RetrieverResult]) -> str:
    header = (f"| corpus | retriever | docs | queries | P@{K} | R@{K} | MRR | p50 us | p95 us | build ms |\n"
              "|---|---|---|---|---|---|---|---|---|---|")
    rows = [f"| {r.corpus} | {r.retriever} | {r.documents} | {r.queries} | {r.precision_at_k} | {r.recall_at_k} | "
            f"{r.mrr} | {r.latency_p50_us} | {r.latency_p95_us} | {r.build_ms} |" for r in results]
    return "\n".join([header, *rows])
