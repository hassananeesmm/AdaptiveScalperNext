"""Direct OKF lookup and keyword search.

Deliberately simple and dependency-free: the curated bundle is tens to a
few hundred concepts, and structured lookup (by id, type, tag, symbol,
strategy) answers most questions exactly. `search()` is a weighted term
match (title x3, tags/description x2, body x1) with no learned
parameters; `docs/KNOWLEDGE_MEMORY.md` benchmarks it against the RAG
TF-IDF index and SQLite FTS5/BM25.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from adaptive_scalper.knowledge.model import STATUS_DEPRECATED, Concept, KnowledgeBundle

_TOKEN = re.compile(r"[a-z0-9_]{2,}")
_STOP = frozenset("the and for with that this from are was were not but you your its into only when then than "
                  "has have had any all can may must never one two per via".split())


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


@dataclass(frozen=True)
class SearchHit:
    concept: Concept
    score: float


def lookup(bundle: KnowledgeBundle, key: str) -> Concept | None:
    """By explicit `id`, bundle path, or path without `.md`."""
    return bundle.by_id(key.lstrip("/"))


def usable(bundle: KnowledgeBundle, concept: Concept, *, include_deprecated: bool = False) -> bool:
    """Concepts with validation ERRORs are quarantined from every consumer."""
    if bundle.errors_for(concept.path):
        return False
    return include_deprecated or concept.status != STATUS_DEPRECATED


def filter_concepts(
    bundle: KnowledgeBundle, *, concept_type: str | None = None, tag: str | None = None,
    canonical_symbol: str | None = None, strategy_key: str | None = None, include_deprecated: bool = False,
) -> list[Concept]:
    out = []
    for concept in bundle.concepts.values():
        if not usable(bundle, concept, include_deprecated=include_deprecated):
            continue
        if concept_type is not None and concept.type != concept_type:
            continue
        if tag is not None and tag not in concept.tags:
            continue
        if canonical_symbol is not None and concept.symbols and canonical_symbol not in concept.symbols:
            continue
        if strategy_key is not None and strategy_key not in concept.strategies:
            continue
        out.append(concept)
    return sorted(out, key=lambda c: c.path)


def search(
    bundle: KnowledgeBundle, query: str, *, top_k: int = 5, concept_type: str | None = None,
    include_deprecated: bool = False,
) -> list[SearchHit]:
    terms = set(tokenize(query))
    if not terms:
        return []
    hits = []
    for concept in bundle.concepts.values():
        if not usable(bundle, concept, include_deprecated=include_deprecated):
            continue
        if concept_type is not None and concept.type != concept_type:
            continue
        fields = (
            (3.0, tokenize(concept.title) + tokenize(concept.id.replace("/", " "))),
            (2.0, tokenize(" ".join(concept.tags)) + tokenize(" ".join(concept.strategies + concept.symbols))),
            (2.0, tokenize(concept.description)),
            (1.0, tokenize(concept.body)),
        )
        score = 0.0
        for weight, tokens in fields:
            if not tokens:
                continue
            present = terms & set(tokens)
            score += weight * len(present) / len(terms)
        if score > 0:
            hits.append(SearchHit(concept, round(score, 4)))
    hits.sort(key=lambda h: (-h.score, h.concept.path))
    return hits[:top_k]


def concept_summary(concept: Concept, now: datetime) -> dict:
    return {
        "id": concept.id, "path": concept.path, "type": concept.type, "title": concept.title,
        "status": concept.status, "trust_tier": concept.trust_tier, "version": concept.version,
        "stale": concept.is_stale(now), "sha256": concept.sha256[:16],
    }
