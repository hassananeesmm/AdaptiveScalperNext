-- Prospective SHADOW observer (docs/audits/PROFITABILITY_RECOVERY_FINAL_REPORT.md,
-- section "Shadow observer"). Forward-only evidence collection after the H1-H9
-- research freeze: it never sends, checks or modifies an order.
--
-- shadow_candidates: one row per strategy candidate per closed decision bar,
--   INCLUDING candidates the selector rejected or never evaluated (global
--   block), with the complete causal feature vector known at the decision bar's
--   close. Nothing in a candidate row can depend on a later bar.
-- shadow_outcomes: one row per (candidate, horizon), inserted only once the
--   horizon has fully elapsed in CLOSED bars. Prices are bar mid prices (the
--   fill model's convention); the entry reference is the open of the first bar
--   at/after the decision time (the earliest causal fill). gross_r is the
--   theoretical mid-to-mid move in units of the candidate's own stop distance;
--   net_r_estimated subtracts the decision-time full round-trip cost estimate
--   (spread counted once there, never again here). A horizon the bar resolution
--   cannot measure is recorded as UNSUPPORTED_RESOLUTION, never interpolated.
-- Both tables are append-only (triggers below): rows are evidence, not state.
-- raw_score is the strategy's heuristic score; the CHECK makes it impossible
-- to store it as a probability.
CREATE TABLE shadow_candidates (
    id                              INTEGER PRIMARY KEY,
    candidate_key                   TEXT NOT NULL UNIQUE,  -- <symbol>:<resolution>:<bar_time>:<strategy>:<direction>
    observer_version                TEXT NOT NULL,
    observed_at_utc                 INTEGER NOT NULL,      -- wall clock when written
    mode                            TEXT NOT NULL CHECK (mode IN ('DEMO', 'PAPER')),
    canonical_symbol                TEXT NOT NULL,
    resolution                      TEXT NOT NULL,
    bar_seconds                     INTEGER NOT NULL CHECK (bar_seconds > 0),
    decision_bar_time_utc           INTEGER NOT NULL,      -- open time of the closed signal bar
    decision_time_utc               INTEGER NOT NULL,      -- that bar's close = earliest causal entry
    strategy_key                    TEXT NOT NULL,
    strategy_version                INTEGER NOT NULL,
    direction                       TEXT NOT NULL CHECK (direction IN ('BUY', 'SELL')),
    raw_score                       REAL NOT NULL,
    raw_score_is_probability        INTEGER NOT NULL DEFAULT 0 CHECK (raw_score_is_probability = 0),
    raw_regime                      TEXT,
    confirmed_regime                TEXT,
    regime_confidence               REAL,
    session                         TEXT,
    news_status                     TEXT,
    news_detail                     TEXT,
    spread_points                   REAL,
    spread_percentile               REAL,
    atr                             REAL,
    realized_volatility             REAL,
    movement_to_cost                REAL,
    stop_distance                   REAL NOT NULL CHECK (stop_distance > 0),
    target_distance                 REAL NOT NULL CHECK (target_distance > 0),
    expected_duration_seconds       INTEGER,
    estimated_round_trip_cost_price REAL,                  -- NULL = cost unknown (BLOCK_COST)
    cost_horizon                    TEXT,
    cost_provenance                 TEXT,
    edge_model                      TEXT NOT NULL,
    scheduler_lag_seconds           REAL,
    selector_disposition            TEXT NOT NULL CHECK (selector_disposition IN
                                        ('SELECTED', 'LOST_TO_HIGHER_EDGE', 'REJECTED', 'NOT_EVALUATED')),
    rejection_reason                TEXT,
    model_observer_score            REAL,
    features_json                   TEXT NOT NULL
);
CREATE INDEX idx_shadow_candidates_symbol_time ON shadow_candidates(canonical_symbol, decision_time_utc);

CREATE TABLE shadow_outcomes (
    id                      INTEGER PRIMARY KEY,
    candidate_id            INTEGER NOT NULL REFERENCES shadow_candidates(id),
    horizon_seconds         INTEGER NOT NULL CHECK (horizon_seconds > 0),
    status                  TEXT NOT NULL CHECK (status IN ('RESOLVED', 'UNSUPPORTED_RESOLUTION', 'INSUFFICIENT_DATA')),
    observer_version        TEXT NOT NULL,
    resolved_at_utc         INTEGER NOT NULL,
    entry_time_utc          INTEGER,
    entry_reference_price   REAL,
    exit_reference_price    REAL,
    gross_r                 REAL,
    net_r_estimated         REAL,
    mfe_r                   REAL,
    mae_r                   REAL,
    time_to_mfe_seconds     INTEGER,
    time_to_mae_seconds     INTEGER,
    first_touch             TEXT CHECK (first_touch IN ('STOP', 'TARGET', 'NEITHER', 'BOTH_SAME_BAR_STOP_ASSUMED')),
    first_touch_seconds     INTEGER,
    bars_used               INTEGER,
    UNIQUE (candidate_id, horizon_seconds)
);

CREATE TRIGGER trg_shadow_candidates_no_update BEFORE UPDATE ON shadow_candidates
BEGIN SELECT RAISE(ABORT, 'shadow_candidates is append-only'); END;
CREATE TRIGGER trg_shadow_candidates_no_delete BEFORE DELETE ON shadow_candidates
BEGIN SELECT RAISE(ABORT, 'shadow_candidates is append-only'); END;
CREATE TRIGGER trg_shadow_outcomes_no_update BEFORE UPDATE ON shadow_outcomes
BEGIN SELECT RAISE(ABORT, 'shadow_outcomes is append-only'); END;
CREATE TRIGGER trg_shadow_outcomes_no_delete BEFORE DELETE ON shadow_outcomes
BEGIN SELECT RAISE(ABORT, 'shadow_outcomes is append-only'); END;
