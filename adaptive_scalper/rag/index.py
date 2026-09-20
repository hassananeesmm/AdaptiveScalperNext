"""TF-IDF retrieval index over RAG memories (CPU-friendly local retrieval).

`rag_memories` (SQLite) is authoritative; this index is a derived,
REBUILDABLE, in-memory artifact — never itself a source of truth. There
is no on-disk index file to go stale: `RagIndex.rebuild()` re-fits a
fresh `TfidfVectorizer` against every memory's `content_text` on demand,
which is cheap at the row counts this project deals with (thousands, not
millions).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from adaptive_scalper.rag.store import RagMemory, get_all_memories


@dataclass(frozen=True)
class SimilarityMatch:
    memory: RagMemory
    score: float


class RagIndex:
    def __init__(self) -> None:
        self._vectorizer: TfidfVectorizer | None = None
        self._matrix = None
        self._memories: list[RagMemory] = []

    @property
    def is_built(self) -> bool:
        return self._vectorizer is not None

    @property
    def size(self) -> int:
        return len(self._memories)

    def rebuild(self, conn: sqlite3.Connection, *, memory_type: str | None = None) -> None:
        """Re-fits from scratch against the CURRENT `rag_memories` table
        state — always call this after writing new memories the caller
        wants reflected in subsequent `query()` calls; nothing here
        auto-refreshes on write, matching `rag rebuild-index` being its
        own explicit CLI command."""
        self._memories = get_all_memories(conn, memory_type=memory_type)
        if not self._memories:
            self._vectorizer = None
            self._matrix = None
            return
        self._vectorizer = TfidfVectorizer(stop_words="english")
        self._matrix = self._vectorizer.fit_transform([m.content_text for m in self._memories])

    def query(self, text: str, *, top_k: int = 5) -> list[SimilarityMatch]:
        if not self.is_built:
            return []
        query_vec = self._vectorizer.transform([text])
        scores = cosine_similarity(query_vec, self._matrix)[0]
        ranked = sorted(zip(self._memories, scores), key=lambda pair: pair[1], reverse=True)
        return [SimilarityMatch(memory, float(score)) for memory, score in ranked[:top_k] if score > 0.0]
