-- Close-side UNKNOWN durability (master prompt section 13).
--
-- Every broker-mutating close is recorded HERE BEFORE order_send is called
-- (status UNRESOLVED, send_outcome SENDING). The row is resolved only by
-- positive evidence: a proven send outcome, or fresh broker truth
-- (positions, working orders, deal history). A process that dies between
-- the write and the send, a lost acknowledgement, an ambiguous retcode or
-- broker truth unavailable right after the send all leave the row
-- UNRESOLVED -- which blocks new exposure and forbids another close for
-- that position (no blind resend) until later broker truth decides.
-- Purely additive: no existing row is changed or deleted.
CREATE TABLE close_requests (
    id                      INTEGER PRIMARY KEY,
    position_id             INTEGER REFERENCES positions(id),
    broker_position_id      TEXT NOT NULL,
    broker_symbol           TEXT NOT NULL,
    position_direction      TEXT NOT NULL CHECK (position_direction IN ('BUY', 'SELL')),
    requested_volume        REAL NOT NULL CHECK (requested_volume > 0),
    magic                   INTEGER NOT NULL,
    comment                 TEXT NOT NULL,
    requested_at_utc        INTEGER NOT NULL,
    send_outcome            TEXT NOT NULL,
    send_detail             TEXT,
    retcode                 INTEGER,
    status                  TEXT NOT NULL CHECK (status IN (
        'UNRESOLVED', 'RESOLVED_CLOSED', 'RESOLVED_PARTIALLY_CLOSED', 'RESOLVED_STILL_OPEN', 'RESOLVED_NOT_EXECUTED'
    )),
    attempt_count           INTEGER NOT NULL DEFAULT 0,
    last_attempt_at_utc     INTEGER,
    last_attempt_detail     TEXT,
    resolved_at_utc         INTEGER,
    resolution_detail       TEXT,
    incident_id             INTEGER REFERENCES execution_incidents(id)
);

-- At most one unresolved close per broker position: a second close can
-- never be recorded (and so never sent) while the first is unproven.
CREATE UNIQUE INDEX idx_close_requests_one_unresolved
    ON close_requests (broker_position_id) WHERE status = 'UNRESOLVED';
CREATE INDEX idx_close_requests_status ON close_requests (status);
