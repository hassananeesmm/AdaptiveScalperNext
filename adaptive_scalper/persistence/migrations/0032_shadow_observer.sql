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
--
-- Candidate IDENTITY (release hardening): one row per
-- (mode, symbol, resolution, decision bar, strategy key, strategy VERSION,
-- direction, observer VERSION, lifecycle VERSION) -- enforced by a composite
-- UNIQUE constraint, not only by the derived candidate_key string. PAPER and
-- DEMO, v1 and v2 of a strategy, two observer or lifecycle versions, opposite
-- directions and different resolutions can therefore never collapse into one
-- row; re-recording the same identity is a no-op (INSERT OR IGNORE).
CREATE TABLE shadow_candidates (
    id                              INTEGER PRIMARY KEY,
    candidate_key                   TEXT NOT NULL UNIQUE,  -- shadow/observer.py candidate_identity_key()
    observer_version                TEXT NOT NULL,
    lifecycle_version               TEXT NOT NULL,         -- shadow/lifecycle.py, or UNDECLARED
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
    -- Final permission is evaluated only by the entry path (broker-truth reads)
    -- and only for a SELECTED candidate: NOT_REACHED for everything else, or
    -- DECIDED_IN_ENTRY_DECISIONS -> join entry_decisions / journal on chain_key.
    final_permission_result         TEXT NOT NULL CHECK (final_permission_result IN
                                        ('NOT_REACHED', 'DECIDED_IN_ENTRY_DECISIONS')),
    chain_key                       TEXT NOT NULL,
    model_observer_score            REAL,
    features_json                   TEXT NOT NULL,
    UNIQUE (mode, canonical_symbol, resolution, decision_bar_time_utc, strategy_key, strategy_version,
            direction, observer_version, lifecycle_version)
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

-- An outcome is computed by the observer version that recorded its candidate.
CREATE TRIGGER trg_shadow_outcomes_observer_version BEFORE INSERT ON shadow_outcomes
WHEN NEW.observer_version IS NOT (SELECT observer_version FROM shadow_candidates WHERE id = NEW.candidate_id)
BEGIN SELECT RAISE(ABORT, 'shadow outcome observer_version must equal its candidate''s'); END;

-- shadow_lifecycle_outcomes: one row per (candidate, lifecycle version,
-- evaluator version) -- the exit the EXECUTABLE
-- position-management lifecycle would have taken (shadow/lifecycle_counterfactual.py,
-- the same exit functions as backtest/PAPER, the same decision core as DEMO),
-- written only once that counterfactual exit has happened in closed bars.
-- R = multiples of the candidate's own stop distance; net_r NULL = a required
-- cost is unknown (never priced as zero).
CREATE TABLE shadow_lifecycle_outcomes (
    id                      INTEGER PRIMARY KEY,
    candidate_id            INTEGER NOT NULL REFERENCES shadow_candidates(id),
    status                  TEXT NOT NULL CHECK (status IN ('RESOLVED', 'INSUFFICIENT_DATA')),
    evaluator_version       TEXT NOT NULL,
    lifecycle_version       TEXT NOT NULL,
    resolved_at_utc         INTEGER NOT NULL,
    entry_time_utc          INTEGER,
    entry_price             REAL,                 -- executable side incl. slippage
    initial_stop_price      REAL,
    initial_target_price    REAL,
    stop_distance_price     REAL,                 -- 1 R
    mfe_r                   REAL,
    mae_r                   REAL,
    time_to_mfe_seconds     INTEGER,
    time_to_mae_seconds     INTEGER,
    exit_time_utc           INTEGER,
    exit_price              REAL,
    exit_reason             TEXT,
    gross_r                 REAL,
    cost_r                  REAL,
    net_r                   REAL,
    holding_seconds         INTEGER,
    cost_provenance         TEXT,
    bars_used               INTEGER,
    UNIQUE (candidate_id, lifecycle_version, evaluator_version)
);

-- A lifecycle outcome evaluates its candidate's OWN lifecycle version.
CREATE TRIGGER trg_shadow_lifecycle_version BEFORE INSERT ON shadow_lifecycle_outcomes
WHEN NEW.lifecycle_version IS NOT (SELECT lifecycle_version FROM shadow_candidates WHERE id = NEW.candidate_id)
BEGIN SELECT RAISE(ABORT, 'shadow lifecycle outcome lifecycle_version must equal its candidate''s'); END;

CREATE TRIGGER trg_shadow_lifecycle_no_update BEFORE UPDATE ON shadow_lifecycle_outcomes
BEGIN SELECT RAISE(ABORT, 'shadow_lifecycle_outcomes is append-only'); END;
CREATE TRIGGER trg_shadow_lifecycle_no_delete BEFORE DELETE ON shadow_lifecycle_outcomes
BEGIN SELECT RAISE(ABORT, 'shadow_lifecycle_outcomes is append-only'); END;

CREATE TRIGGER trg_shadow_candidates_no_update BEFORE UPDATE ON shadow_candidates
BEGIN SELECT RAISE(ABORT, 'shadow_candidates is append-only'); END;
CREATE TRIGGER trg_shadow_candidates_no_delete BEFORE DELETE ON shadow_candidates
BEGIN SELECT RAISE(ABORT, 'shadow_candidates is append-only'); END;
CREATE TRIGGER trg_shadow_outcomes_no_update BEFORE UPDATE ON shadow_outcomes
BEGIN SELECT RAISE(ABORT, 'shadow_outcomes is append-only'); END;
CREATE TRIGGER trg_shadow_outcomes_no_delete BEFORE DELETE ON shadow_outcomes
BEGIN SELECT RAISE(ABORT, 'shadow_outcomes is append-only'); END;
