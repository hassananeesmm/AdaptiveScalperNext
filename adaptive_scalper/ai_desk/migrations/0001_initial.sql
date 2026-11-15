-- AI desk research journal (data/ai_desk.sqlite3). Separate from the trading
-- database: the desk is the only writer here and never writes the trading DB.

CREATE TABLE desk_runs (
    run_id             TEXT PRIMARY KEY,
    trigger            TEXT NOT NULL,
    mode               TEXT NOT NULL CHECK (mode IN ('CURRENT', 'REPLAY')),
    snapshot_id        TEXT,
    started_at_utc     INTEGER NOT NULL,
    finished_at_utc    INTEGER,
    status             TEXT NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED', 'ABANDONED')),
    config_fingerprint TEXT NOT NULL,
    desk_version       TEXT NOT NULL,
    agents_json        TEXT NOT NULL DEFAULT '[]',
    error              TEXT
);
CREATE INDEX idx_desk_runs_started ON desk_runs (started_at_utc);

CREATE TABLE snapshots (
    snapshot_id     TEXT PRIMARY KEY,
    as_of_utc       INTEGER NOT NULL,
    mode            TEXT NOT NULL,
    runtime_live    INTEGER NOT NULL,
    limitations_json TEXT NOT NULL,
    evidence_count  INTEGER NOT NULL,
    created_at_utc  INTEGER NOT NULL
);

-- Bulky, reconstructable evidence (bars, trades, candidates) is stored as a
-- digest (sha + range); the immutable source rows stay in the trading DB and a
-- replay rebuilds them with the same as-of cut-off.
CREATE TABLE evidence (
    snapshot_id        TEXT NOT NULL REFERENCES snapshots (snapshot_id) ON DELETE CASCADE,
    evidence_id        TEXT NOT NULL,
    source             TEXT NOT NULL,
    kind               TEXT NOT NULL,
    symbol             TEXT,
    data_timestamp_utc INTEGER,
    storage            TEXT NOT NULL CHECK (storage IN ('FULL', 'DIGEST')),
    payload_json       TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, evidence_id)
);

CREATE TABLE agent_results (
    analysis_id        TEXT PRIMARY KEY,
    run_id             TEXT NOT NULL,
    agent              TEXT NOT NULL,
    agent_version      TEXT NOT NULL,
    symbol             TEXT,
    snapshot_id        TEXT NOT NULL,
    as_of_utc          INTEGER NOT NULL,
    status             TEXT NOT NULL,
    assessment         TEXT NOT NULL,
    confidence         REAL,
    provider           TEXT NOT NULL,
    model_id           TEXT NOT NULL,
    duration_ms        INTEGER NOT NULL,
    config_fingerprint TEXT NOT NULL,
    validation         TEXT NOT NULL,
    result_json        TEXT NOT NULL,
    created_at_utc     INTEGER NOT NULL
);
CREATE INDEX idx_agent_results_agent ON agent_results (agent, symbol, as_of_utc);
CREATE INDEX idx_agent_results_run ON agent_results (run_id);

-- Historical analyses are append-only: never rewritten (retention may delete).
CREATE TRIGGER agent_results_no_update BEFORE UPDATE ON agent_results
BEGIN
    SELECT RAISE(ABORT, 'agent_results are append-only');
END;

CREATE TABLE disagreements (
    id             INTEGER PRIMARY KEY,
    run_id         TEXT NOT NULL,
    topic          TEXT NOT NULL,
    parties_json   TEXT NOT NULL,
    positions_json TEXT NOT NULL,
    severity       TEXT NOT NULL,
    resolution     TEXT NOT NULL,
    created_at_utc INTEGER NOT NULL,
    UNIQUE (run_id, topic, positions_json)
);

CREATE TABLE provider_calls (
    id             INTEGER PRIMARY KEY,
    run_id         TEXT,
    provider       TEXT NOT NULL,
    model          TEXT NOT NULL,
    agent          TEXT,
    status         TEXT NOT NULL,
    latency_ms     INTEGER NOT NULL,
    input_tokens   INTEGER NOT NULL DEFAULT 0,
    output_tokens  INTEGER NOT NULL DEFAULT 0,
    cost_usd       REAL NOT NULL DEFAULT 0,
    error          TEXT,
    created_at_utc INTEGER NOT NULL
);
CREATE INDEX idx_provider_calls_created ON provider_calls (created_at_utc);

CREATE TABLE narrative_cache (
    cache_key      TEXT PRIMARY KEY,
    response_json  TEXT NOT NULL,
    created_at_utc INTEGER NOT NULL
);

CREATE TABLE processed_events (
    event_key      TEXT PRIMARY KEY,
    event_type     TEXT NOT NULL,
    first_seen_utc INTEGER NOT NULL,
    run_id         TEXT
);

CREATE TABLE watermarks (
    name           TEXT PRIMARY KEY,
    value          TEXT NOT NULL,
    updated_at_utc INTEGER NOT NULL
);

CREATE TABLE forward_checks (
    check_id       TEXT PRIMARY KEY,
    analysis_id    TEXT NOT NULL,
    agent          TEXT NOT NULL,
    symbol         TEXT,
    metric         TEXT NOT NULL,
    expected       TEXT NOT NULL,
    payload_json   TEXT NOT NULL,
    due_at_utc     INTEGER NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('PENDING', 'SCORED', 'UNSCORABLE')),
    outcome_json   TEXT,
    hit            INTEGER,
    scored_at_utc  INTEGER,
    created_at_utc INTEGER NOT NULL
);
CREATE INDEX idx_forward_checks_due ON forward_checks (status, due_at_utc);

CREATE TABLE evaluations (
    evaluation_id    TEXT PRIMARY KEY,
    kind             TEXT NOT NULL,
    protocol_version TEXT NOT NULL,
    protocol_hash    TEXT NOT NULL,
    data_range_json  TEXT NOT NULL,
    result_json      TEXT NOT NULL,
    trial_number     INTEGER NOT NULL,
    created_at_utc   INTEGER NOT NULL
);

CREATE TABLE service_state (
    key            TEXT PRIMARY KEY,
    value_json     TEXT NOT NULL,
    updated_at_utc INTEGER NOT NULL
);
