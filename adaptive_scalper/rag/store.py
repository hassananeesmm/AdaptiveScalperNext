"""RAG memory storage (SQLite-authoritative).

Every write here is a plain insert — there is no update/delete function.
Not DB-trigger-enforced immutable the way `journal_events` is (a
`TRADE_SETUP` memory's metadata legitimately gains a `TRADE_RESULT`
sibling once the position closes, rather than being edited in place),
but nothing in this module offers a way to mutate or remove a row either
— a caller who wants to correct a bad memory records a new one, exactly
like every other append-only store in this codebase.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

MEMORY_TYPES = frozenset({
    "TRADE_SETUP", "TRADE_RESULT", "REJECTION", "EXIT_DECISION",
    "REENTRY_DECISION", "EXECUTION_INCIDENT", "STRATEGY_CONTEXT", "SYSTEM_EVENT",
})


class UnknownMemoryTypeError(ValueError):
    """Raised when `store_memory` is asked to record a type not in `MEMORY_TYPES`."""


@dataclass(frozen=True)
class RagMemory:
    id: int
    memory_type: str
    canonical_symbol: str | None
    strategy_key: str | None
    chain_key: str | None
    content_text: str
    metadata: dict
    created_at_utc: int


def _row_to_memory(row: sqlite3.Row) -> RagMemory:
    return RagMemory(
        id=row["id"], memory_type=row["memory_type"], canonical_symbol=row["canonical_symbol"],
        strategy_key=row["strategy_key"], chain_key=row["chain_key"], content_text=row["content_text"],
        metadata=json.loads(row["metadata_json"]), created_at_utc=row["created_at_utc"],
    )


def store_memory(
    conn: sqlite3.Connection,
    memory_type: str,
    content_text: str,
    metadata: dict,
    *,
    canonical_symbol: str | None = None,
    strategy_key: str | None = None,
    chain_key: str | None = None,
    now_utc: int | None = None,
) -> RagMemory:
    if memory_type not in MEMORY_TYPES:
        raise UnknownMemoryTypeError(f"{memory_type!r} is not a recognized RAG memory type")
    now = now_utc if now_utc is not None else int(time.time())
    cursor = conn.execute(
        """
        INSERT INTO rag_memories
            (memory_type, canonical_symbol, strategy_key, chain_key, content_text, metadata_json, created_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            memory_type, canonical_symbol, strategy_key, chain_key, content_text,
            json.dumps(metadata, default=str), now,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM rag_memories WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _row_to_memory(row)


def get_all_memories(
    conn: sqlite3.Connection,
    *,
    memory_type: str | None = None,
    canonical_symbol: str | None = None,
    limit: int = 10000,
) -> list[RagMemory]:
    query = "SELECT * FROM rag_memories WHERE 1=1"
    params: list = []
    if memory_type is not None:
        query += " AND memory_type = ?"
        params.append(memory_type)
    if canonical_symbol is not None:
        query += " AND canonical_symbol = ?"
        params.append(canonical_symbol)
    query += " ORDER BY created_at_utc DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return [_row_to_memory(r) for r in rows]


def get_memory_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM rag_memories").fetchone()["n"]
