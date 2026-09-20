"""RAG advisory service — the intended public entry point for every other
subsystem (nothing outside `adaptive_scalper/rag/` should import
`rag.store`/`rag.index` directly).

Structurally advisory: every method here returns data (a stored memory,
similarity matches, a status dict) and NONE accepts a `Gateway`, risk
limits, kill-switch state, the symbol allow-list, or the strategy
registry — there is no parameter through which this class COULD execute
an order, raise risk, clear the kill switch, change the tradable symbol
universe, reactivate a retired strategy, or bypass the final permission
gate. A caller wanting to act on a RAG result must route it through the
normal strategy/selector/final-permission pipeline, exactly like any
other signal — RAG only ever informs, never decides.

Failure mode: `DEGRADED`, never an unhandled exception that takes down
the caller. A RAG lookup that fails (missing dependency, empty/corrupt
index, DB error) is exactly as safe to ignore as an empty result — a
caller was never entitled to treat "no RAG context" as fatal, since RAG
is advisory-only by definition.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from adaptive_scalper.rag.index import RagIndex, SimilarityMatch
from adaptive_scalper.rag.store import RagMemory, get_memory_count, store_memory

logger = logging.getLogger(__name__)

OK = "OK"
DEGRADED = "DEGRADED"


@dataclass(frozen=True)
class RagQueryResult:
    status: str   # OK or DEGRADED
    matches: tuple[SimilarityMatch, ...]
    detail: str


class RagService:
    """One `RagIndex` per instance. Rebuild explicitly via
    `rebuild_index()` rather than on every query — callers control the
    cost/freshness tradeoff (matches directive's `rag rebuild-index`
    being its own CLI command, not an implicit side effect of writes)."""

    def __init__(self) -> None:
        self._index = RagIndex()

    def record(
        self,
        conn: sqlite3.Connection,
        memory_type: str,
        content_text: str,
        metadata: dict,
        **kwargs,
    ) -> RagMemory | None:
        """Never raises outward — a failure to record a memory must never
        break the caller's real work (journaling, order handling, position
        management, etc.). Returns `None` on failure, the stored memory
        on success."""
        try:
            return store_memory(conn, memory_type, content_text, metadata, **kwargs)
        except Exception:
            logger.exception("RAG record failed; continuing without it (RAG is advisory-only)")
            return None

    def rebuild_index(self, conn: sqlite3.Connection) -> str:
        try:
            self._index.rebuild(conn)
            return OK
        except Exception:
            logger.exception("RAG index rebuild failed")
            return DEGRADED

    def query_similar(self, text: str, *, top_k: int = 5) -> RagQueryResult:
        if not self._index.is_built:
            return RagQueryResult(DEGRADED, (), "index not built or empty — call rebuild_index() first")
        try:
            matches = self._index.query(text, top_k=top_k)
            return RagQueryResult(OK, tuple(matches), f"{len(matches)} match(es)")
        except Exception as exc:
            logger.exception("RAG query failed")
            return RagQueryResult(DEGRADED, (), f"query failed: {exc}")

    def status(self, conn: sqlite3.Connection) -> dict:
        return {
            "index_built": self._index.is_built,
            "index_size": self._index.size,
            "total_memories": get_memory_count(conn),
        }
