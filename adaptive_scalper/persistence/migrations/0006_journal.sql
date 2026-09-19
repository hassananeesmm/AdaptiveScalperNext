-- 0006_journal: immutable, append-oriented decision journal (directive
-- sections 54-58). One `decision_chains` row per traceable
-- signal->proposal->...->learning chain (directive section 56); many
-- `journal_events` rows per chain, in sequence.
--
-- Event-specific data lives in `payload_json` rather than dozens of
-- mostly-NULL columns: most event types' meaningful fields (model
-- score, RAG contribution, cost estimate, ...) belong to subsystems
-- that don't exist yet. Pre-declaring their columns now would be
-- exactly the kind of permanently-empty "showpiece" schema directive
-- section 118 forbids; each subsystem's journal integration adds real,
-- populated fields to its own events' payloads as it's built.
--
-- Immutability is enforced at TWO layers: the application layer only
-- ever exposes append (`journal/events.py` has no update/delete
-- function), and — defense in depth, since a schema is not a promise a
-- future bug can't break — these triggers make UPDATE/DELETE fail at
-- the database level too.

CREATE TABLE decision_chains (
    id                  INTEGER PRIMARY KEY,
    chain_key           TEXT NOT NULL UNIQUE,
    canonical_symbol    TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE journal_events (
    id                  INTEGER PRIMARY KEY,
    chain_id            INTEGER NOT NULL REFERENCES decision_chains(id),
    sequence_in_chain    INTEGER NOT NULL,
    event_type          TEXT NOT NULL CHECK (event_type IN (
        'SIGNAL_CREATED', 'SIGNAL_REJECTED',
        'PROPOSAL_CREATED', 'PROPOSAL_REJECTED',
        'ENTRY_ALLOWED', 'ENTRY_BLOCKED',
        'ORDER_SUBMITTED', 'ORDER_ACCEPTED', 'ORDER_PENDING', 'ORDER_PARTIAL', 'ORDER_FILLED',
        'ORDER_REJECTED', 'ORDER_CANCELLED', 'ORDER_EXPIRED', 'ORDER_UNKNOWN',
        'POSITION_OPENED', 'POSITION_REVIEWED', 'STOP_ADVANCED', 'POSITION_CLOSED',
        'REENTRY_CONSIDERED', 'REENTRY_ALLOWED', 'REENTRY_REJECTED',
        'NEWS_BLOCK_ENTERED', 'NEWS_BLOCK_CLEARED',
        'MODEL_USED', 'RAG_USED',
        'RECONCILIATION_ACTION', 'LEARNING_UPDATE'
    )),
    event_timestamp_utc INTEGER NOT NULL,   -- when the event actually occurred (decision time)
    recorded_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),  -- when appended (audit time)
    canonical_symbol    TEXT NOT NULL,
    -- Strongly-typed linkage fields (directive section 55): kept as real
    -- indexable columns, not buried in payload_json, because they are
    -- the fields other subsystems (reconciliation, position management)
    -- need to look an event up BY, not just read once they've found it.
    -- All nullable: which ones apply depends on event_type (a
    -- SIGNAL_CREATED has a strategy_key and no broker_order_id; an
    -- ORDER_FILLED has the reverse).
    broker_symbol        TEXT,
    strategy_key         TEXT,
    client_request_id    TEXT,   -- idempotency key (directive section 29)
    broker_order_id      TEXT,
    broker_position_id   TEXT,
    broker_deal_id       TEXT,
    payload_json        TEXT NOT NULL,
    UNIQUE (chain_id, sequence_in_chain)
);

CREATE INDEX idx_journal_events_chain ON journal_events (chain_id, sequence_in_chain);
CREATE INDEX idx_journal_events_type_time ON journal_events (event_type, event_timestamp_utc);
CREATE INDEX idx_journal_events_symbol_time ON journal_events (canonical_symbol, event_timestamp_utc);
CREATE INDEX idx_journal_events_order_id ON journal_events (broker_order_id);
CREATE INDEX idx_journal_events_position_id ON journal_events (broker_position_id);
CREATE INDEX idx_journal_events_request_id ON journal_events (client_request_id);

CREATE TRIGGER trg_journal_events_no_update
BEFORE UPDATE ON journal_events
BEGIN
    SELECT RAISE(ABORT, 'journal_events is append-only: UPDATE is not permitted');
END;

CREATE TRIGGER trg_journal_events_no_delete
BEFORE DELETE ON journal_events
BEGIN
    SELECT RAISE(ABORT, 'journal_events is append-only: DELETE is not permitted');
END;
