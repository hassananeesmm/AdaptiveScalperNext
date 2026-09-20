-- 0011_learning: ML/self-learning model registry (directive: observer
-- stage first, no source self-modification, no eval/exec, CPU-friendly
-- models only). A model's lifecycle state is tracked exactly like an
-- order's (directive section 29's pattern reused here): every legal
-- transition goes through a state machine and is recorded, never a bare
-- UPDATE.

CREATE TABLE models (
    id                      INTEGER PRIMARY KEY,
    model_key                TEXT NOT NULL,
    version                    INTEGER NOT NULL,
    strategy_key                 TEXT,
    lifecycle_state                TEXT NOT NULL CHECK (lifecycle_state IN (
        'BASELINE', 'CHALLENGER', 'CURRENT', 'PREVIOUS_STABLE',
        'REJECTED', 'DEGRADED', 'ROLLED_BACK', 'INSUFFICIENT_DATA'
    )),
    artifact_path                    TEXT,
    artifact_checksum                  TEXT,
    training_sample_count                INTEGER NOT NULL DEFAULT 0,
    metrics_json                           TEXT,
    created_at_utc                           INTEGER NOT NULL,
    updated_at_utc                             INTEGER NOT NULL,
    UNIQUE (model_key, version)
);

CREATE INDEX idx_models_key_state ON models (model_key, lifecycle_state);
CREATE INDEX idx_models_strategy ON models (strategy_key);

CREATE TABLE model_lifecycle_transitions (
    id                  INTEGER PRIMARY KEY,
    model_id             INTEGER NOT NULL REFERENCES models(id),
    from_state             TEXT,
    to_state                 TEXT NOT NULL,
    occurred_at_utc             INTEGER NOT NULL,
    detail                        TEXT
);

CREATE INDEX idx_model_lifecycle_transitions_model ON model_lifecycle_transitions (model_id, occurred_at_utc);
