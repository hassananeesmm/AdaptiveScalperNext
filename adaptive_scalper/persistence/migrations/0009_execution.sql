-- 0009_execution: order state machine, deals, positions, and execution
-- incidents (directive sections 29-31). `order_send` does not exist
-- anywhere in this codebase yet — this schema exists to receive its
-- result safely once it does.
--
-- Idempotency (directive section 29): `client_request_id` is UNIQUE.
-- Creating an order for a `client_request_id` that already has a row
-- must be a no-op that returns the EXISTING row, never a second order —
-- this is what makes a retried submission safe.

CREATE TABLE orders (
    id                      INTEGER PRIMARY KEY,
    client_request_id       TEXT NOT NULL UNIQUE,
    chain_key                TEXT,
    canonical_symbol         TEXT NOT NULL,
    broker_symbol             TEXT NOT NULL,
    direction                  TEXT NOT NULL CHECK (direction IN ('BUY', 'SELL')),
    requested_volume           REAL NOT NULL,
    stop_loss                   REAL,
    take_profit                  REAL,
    state                         TEXT NOT NULL CHECK (state IN (
        'PROPOSED', 'SUBMITTED', 'ACCEPTED', 'PENDING', 'RESTING', 'PARTIAL',
        'FILLED', 'REJECTED', 'CANCELLED', 'EXPIRED', 'UNKNOWN', 'PENDING_RECONCILIATION'
    )),
    broker_order_id               TEXT,
    broker_position_id             TEXT,
    last_broker_retcode              INTEGER,
    last_broker_comment               TEXT,
    raw_broker_response_json           TEXT,
    created_at_utc                      INTEGER NOT NULL,
    updated_at_utc                       INTEGER NOT NULL
);

CREATE INDEX idx_orders_state ON orders (state);
CREATE INDEX idx_orders_broker_order_id ON orders (broker_order_id);
CREATE INDEX idx_orders_broker_position_id ON orders (broker_position_id);
CREATE INDEX idx_orders_chain_key ON orders (chain_key);

CREATE TABLE order_state_transitions (
    id                  INTEGER PRIMARY KEY,
    order_id             INTEGER NOT NULL REFERENCES orders(id),
    from_state            TEXT,
    to_state               TEXT NOT NULL,
    occurred_at_utc         INTEGER NOT NULL,
    detail                   TEXT
);

CREATE INDEX idx_order_state_transitions_order ON order_state_transitions (order_id, occurred_at_utc);

CREATE TABLE deals (
    id                  INTEGER PRIMARY KEY,
    order_id             INTEGER REFERENCES orders(id),
    broker_deal_id        TEXT NOT NULL UNIQUE,
    broker_position_id     TEXT,
    price                    REAL NOT NULL,
    volume                    REAL NOT NULL,
    commission                 REAL NOT NULL,
    swap                        REAL NOT NULL,
    profit                       REAL NOT NULL,
    occurred_at_utc               INTEGER NOT NULL
);

CREATE INDEX idx_deals_position ON deals (broker_position_id);

CREATE TABLE positions (
    id                      INTEGER PRIMARY KEY,
    broker_position_id       TEXT NOT NULL UNIQUE,
    canonical_symbol           TEXT NOT NULL,
    direction                    TEXT NOT NULL CHECK (direction IN ('BUY', 'SELL')),
    volume                        REAL NOT NULL,
    entry_price                     REAL NOT NULL,
    initial_monetary_risk             REAL NOT NULL,
    strategy_key                       TEXT,
    entry_order_id                       INTEGER REFERENCES orders(id),
    status                                 TEXT NOT NULL CHECK (status IN ('OPEN', 'CLOSED')),
    opened_at_utc                           INTEGER NOT NULL,
    closed_at_utc                             INTEGER
);

CREATE INDEX idx_positions_status ON positions (status);
CREATE INDEX idx_positions_symbol ON positions (canonical_symbol, status);

-- Directive section 30: an order/close outcome that cannot currently be
-- determined must never be silently ignored or blindly resent. Every
-- UNKNOWN occurrence — and every reconciliation mismatch found later —
-- gets a durable row here, resolved (or not) explicitly.
CREATE TABLE execution_incidents (
    id                  INTEGER PRIMARY KEY,
    order_id             INTEGER REFERENCES orders(id),
    incident_type         TEXT NOT NULL CHECK (incident_type IN (
        'UNKNOWN_OUTCOME', 'RECONCILIATION_MISMATCH', 'ORPHAN_BROKER_POSITION', 'MISSING_LOCAL_POSITION'
    )),
    detail                 TEXT NOT NULL,
    detected_at_utc         INTEGER NOT NULL,
    resolved_at_utc          INTEGER,
    resolution                TEXT
);

CREATE INDEX idx_execution_incidents_unresolved ON execution_incidents (resolved_at_utc);
