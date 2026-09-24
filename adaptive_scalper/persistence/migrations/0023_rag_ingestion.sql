-- 0023_rag_ingestion: journal -> RAG ingestion (directive section 58).
-- `source_key` names the authoritative row a memory was derived from
-- (e.g. "journal:1234", "paper_trade:17", "incident:5"); the partial
-- unique index makes ingestion idempotent. `origin` is the evidence class
-- (BROKER_DEMO_CONFIRMED / PAPER_LIVE_DATA / BACKTEST / DECISION / SYSTEM)
-- so PAPER and DEMO memories are never silently pooled (section 82).
ALTER TABLE rag_memories ADD COLUMN source_key TEXT;
ALTER TABLE rag_memories ADD COLUMN origin TEXT;
CREATE UNIQUE INDEX idx_rag_memories_source_key ON rag_memories (source_key) WHERE source_key IS NOT NULL;

-- Per-source high-water marks so each ingestion pass only reads new rows.
CREATE TABLE rag_ingestion_state (
    source          TEXT PRIMARY KEY,
    last_id           INTEGER NOT NULL,
    updated_at_utc      INTEGER NOT NULL
);
