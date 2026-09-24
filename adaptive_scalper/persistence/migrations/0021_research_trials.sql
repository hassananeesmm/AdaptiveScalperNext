-- 0021_research_trials: the research trial ledger. EVERY configuration
-- that was tried (strategy version, feature set, model, parameters,
-- dataset, split, cost model) is recorded with its result, including the
-- ones that failed or were abandoned. The count of trials per research
-- family is what the Deflated Sharpe Ratio deflates by; forgotten failed
-- experiments are exactly the hidden selection bias it exists to expose.
-- Append-only: results are never edited, a re-run is a new trial.
CREATE TABLE research_trials (
    id                  INTEGER PRIMARY KEY,
    trial_id              TEXT NOT NULL UNIQUE,
    family                   TEXT NOT NULL,      -- groups trials competing for the same selection
    created_at_utc              INTEGER NOT NULL,
    kind                           TEXT NOT NULL,   -- BACKTEST / SEQUENTIAL_FOLDS / PURGED_CV / CPCV / MODEL_WALK_FORWARD / ...
    strategy_versions_json            TEXT NOT NULL,
    feature_set                          TEXT,
    model                                   TEXT,
    params_json                                TEXT NOT NULL,
    dataset_id                                    TEXT,
    split_config_json                                TEXT,
    cost_model_json                                     TEXT,
    status                                                 TEXT NOT NULL CHECK (status IN ('COMPLETED', 'FAILED', 'ABANDONED')),
    sharpe                                                    REAL,
    n_observations                                               INTEGER,
    result_json                                                     TEXT,
    notes                                                              TEXT
);

CREATE INDEX idx_research_trials_family ON research_trials (family, created_at_utc);

CREATE TRIGGER research_trials_no_update BEFORE UPDATE ON research_trials
BEGIN
    SELECT RAISE(ABORT, 'research_trials is append-only');
END;

CREATE TRIGGER research_trials_no_delete BEFORE DELETE ON research_trials
BEGIN
    SELECT RAISE(ABORT, 'research_trials is append-only');
END;
