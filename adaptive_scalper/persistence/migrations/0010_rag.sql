-- 0010_rag: local RAG memory store (directive: local-only RAG, SQLite
-- authoritative, rebuildable index). RAG is ADVISORY only — nothing in
-- this schema or the code that reads/writes it may execute, raise risk,
-- clear the kill switch, change the symbol universe, reactivate a
-- retired strategy, or bypass the final permission gate; it is pure
-- retrieval over past decisions/outcomes for a human/strategy to
-- consider, never a decision-maker itself.

CREATE TABLE rag_memories (
    id                  INTEGER PRIMARY KEY,
    memory_type         TEXT NOT NULL CHECK (memory_type IN (
        'TRADE_SETUP', 'TRADE_RESULT', 'REJECTION', 'EXIT_DECISION',
        'REENTRY_DECISION', 'EXECUTION_INCIDENT', 'STRATEGY_CONTEXT', 'SYSTEM_EVENT'
    )),
    canonical_symbol    TEXT,
    strategy_key         TEXT,
    chain_key             TEXT,
    -- The retrievable text this memory is indexed on (a compact, human/
    -- LLM-readable summary — never raw broker credentials or secrets).
    content_text           TEXT NOT NULL,
    -- Structured fields (outcome, R-multiple, rejection reason, etc.),
    -- opaque to the retrieval layer, JSON-encoded.
    metadata_json            TEXT NOT NULL,
    created_at_utc             INTEGER NOT NULL
);

CREATE INDEX idx_rag_memories_type ON rag_memories (memory_type, created_at_utc);
CREATE INDEX idx_rag_memories_symbol ON rag_memories (canonical_symbol, created_at_utc);
CREATE INDEX idx_rag_memories_chain_key ON rag_memories (chain_key);
