-- 0012_position_management: durable continuous position-management state
-- (directive: "COMPLETE POSITION PERSISTENCE"). initial_monetary_risk is
-- written ONCE at creation and never updated by any statement in this
-- schema or the module that owns it — moving a protective stop must
-- never redefine R (directive section 21). peak_r persists across a
-- process restart so a giveback check can never be fooled by a
-- freshly-restarted-and-therefore-"reset" peak.

CREATE TABLE position_management_state (
    id                      INTEGER PRIMARY KEY,
    position_id               INTEGER NOT NULL UNIQUE REFERENCES positions(id),
    initial_monetary_risk       REAL NOT NULL,
    entry_regime                  TEXT NOT NULL,
    latest_regime                   TEXT NOT NULL,
    peak_r                            REAL NOT NULL DEFAULT 0.0,
    current_r                          REAL,
    last_review_at_utc                   INTEGER,
    threshold_cross_at_utc                 INTEGER,
    decision_at_utc                          INTEGER,
    request_at_utc                             INTEGER,
    broker_response_at_utc                       INTEGER,
    decision_r                                     REAL,
    fill_r                                           REAL,
    giveback_decision                                  REAL,
    giveback_fill                                        REAL,
    expected_slippage                                      REAL,
    realized_slippage                                        REAL,
    created_at_utc                                             INTEGER NOT NULL,
    updated_at_utc                                               INTEGER NOT NULL
);

CREATE INDEX idx_position_management_state_position ON position_management_state (position_id);
