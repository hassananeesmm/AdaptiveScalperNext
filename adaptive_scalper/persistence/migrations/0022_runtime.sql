-- 0022_runtime: state the live runtime publishes for the observer-only
-- dashboard/CLI, the deduplicated operator event stream, every new-entry
-- decision (the source of why-no-trade and of PAPER/DEMO acceptance
-- metrics), and the entry context a live position needs for review.

-- Small keyed JSON documents: engine heartbeat, component health, the
-- latest why-no-trade snapshot, per-symbol market snapshots, persisted
-- DEMO regime trackers / last decided bar / peak equity.
CREATE TABLE runtime_state (
    key             TEXT PRIMARY KEY,
    value_json        TEXT NOT NULL,
    updated_at_utc      INTEGER NOT NULL
);

-- Directive sections 103/109: INFO/WARNING/BLOCKED/ERROR/CRITICAL,
-- deduplicated -- a condition that persists is ONE row whose
-- occurrence_count/last_seen advance, not one row per cycle.
CREATE TABLE runtime_events (
    id                  INTEGER PRIMARY KEY,
    severity              TEXT NOT NULL CHECK (severity IN ('INFO', 'WARNING', 'BLOCKED', 'ERROR', 'CRITICAL')),
    component                TEXT NOT NULL,
    event                       TEXT NOT NULL,
    canonical_symbol               TEXT,
    detail                            TEXT NOT NULL,
    dedup_key                            TEXT,
    first_seen_at_utc                       INTEGER NOT NULL,
    last_seen_at_utc                           INTEGER NOT NULL,
    occurrence_count                              INTEGER NOT NULL DEFAULT 1,
    cleared_at_utc                                   INTEGER
);

CREATE INDEX idx_runtime_events_recent ON runtime_events (last_seen_at_utc);
CREATE INDEX idx_runtime_events_open ON runtime_events (dedup_key, cleared_at_utc);

-- One row per new-entry decision outcome (allowed, blocked, rejected,
-- submitted, filled...). mode separates PAPER from DEMO evidence.
CREATE TABLE entry_decisions (
    id                  INTEGER PRIMARY KEY,
    decided_at_utc        INTEGER NOT NULL,
    mode                     TEXT NOT NULL CHECK (mode IN ('PAPER', 'DEMO')),
    canonical_symbol            TEXT,
    bar_time_utc                   INTEGER,
    strategy_key                      TEXT,
    direction                            TEXT,
    stage                                   TEXT NOT NULL,   -- GLOBAL / SIGNAL / SELECTOR / SIZING / PERMISSION / EXECUTION / PAPER_FILL
    decision                                   TEXT NOT NULL,   -- ALLOW / BLOCK_* / FLAT / FILLED / ...
    reason                                        TEXT NOT NULL,
    chain_key                                        TEXT,
    detail_json                                         TEXT
);

CREATE INDEX idx_entry_decisions_time ON entry_decisions (decided_at_utc);

-- The decision-time context a live DEMO position is reviewed against
-- (entry regime, original strategy/version/confidence, original stop
-- and target distances). Written when an entry fills.
CREATE TABLE position_entry_context (
    broker_position_id   TEXT PRIMARY KEY,
    canonical_symbol        TEXT NOT NULL,
    strategy_key               TEXT NOT NULL,
    strategy_version              INTEGER,
    direction                        TEXT NOT NULL,
    entry_regime                        TEXT NOT NULL,
    raw_confidence                         REAL,
    stop_distance_price                       REAL NOT NULL,
    target_distance_price                        REAL NOT NULL,
    signal_bar_time_utc                             INTEGER,
    chain_key                                          TEXT,
    recorded_at_utc                                       INTEGER NOT NULL
);
