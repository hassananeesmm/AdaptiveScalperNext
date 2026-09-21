-- 0014_backtest: causal backtest/walk-forward/OOS/Monte Carlo research
-- infrastructure (directive section 80, "BACKTEST / WALK-FORWARD / OOS").
--
-- `datasets`/`dataset_usage` are directive section 53/65's dataset
-- integrity tables: every dataset snapshot a backtest, walk-forward fold,
-- or (future) ML training run consumes is recorded with its own id,
-- checksum, and range split -- "An OOS dataset that influenced design is
-- no longer untouched" is enforced by recording USAGE, not just existence.
--
-- `backtest_runs`/`backtest_trades` persist actual results so the CLI/
-- dashboard can query history later, labeled with `origin` (directive:
-- never silently mix BACKTEST with PAPER/BROKER_DEMO_CONFIRMED evidence).

CREATE TABLE datasets (
    id                          INTEGER PRIMARY KEY,
    dataset_id                    TEXT NOT NULL UNIQUE,
    created_at_utc                   INTEGER NOT NULL,
    canonical_symbol                    TEXT NOT NULL,
    resolution                             TEXT NOT NULL,
    strategies_json                           TEXT NOT NULL,
    origin                                       TEXT NOT NULL,
    account_scope                                   TEXT,
    feature_schema_version                             INTEGER NOT NULL,
    label_version                                         INTEGER,
    row_count                                                INTEGER NOT NULL,
    excluded_row_count                                          INTEGER NOT NULL DEFAULT 0,
    range_start_utc                                                INTEGER NOT NULL,
    range_end_utc                                                     INTEGER NOT NULL,
    training_range_start_utc                                            INTEGER,
    training_range_end_utc                                                 INTEGER,
    validation_range_start_utc                                                INTEGER,
    validation_range_end_utc                                                     INTEGER,
    oos_range_start_utc                                                             INTEGER,
    oos_range_end_utc                                                                  INTEGER,
    checksum                                                                              TEXT NOT NULL
);

CREATE INDEX idx_datasets_symbol ON datasets (canonical_symbol, resolution);

-- Recording that a dataset was USED (and for what) is what lets a later
-- check ask "has this OOS slice ever been touched before?" -- existence
-- of the datasets row alone does not prove that.
CREATE TABLE dataset_usage (
    id                  INTEGER PRIMARY KEY,
    dataset_id            TEXT NOT NULL REFERENCES datasets(dataset_id),
    used_by_run_id           TEXT NOT NULL,
    used_for                    TEXT NOT NULL CHECK (used_for IN (
        'TRAINING', 'VALIDATION', 'OOS', 'WALK_FORWARD_FOLD', 'MONTE_CARLO'
    )),
    used_at_utc                     INTEGER NOT NULL
);

CREATE INDEX idx_dataset_usage_dataset ON dataset_usage (dataset_id);

CREATE TABLE backtest_runs (
    id                  INTEGER PRIMARY KEY,
    run_id                TEXT NOT NULL UNIQUE,
    created_at_utc           INTEGER NOT NULL,
    canonical_symbol            TEXT NOT NULL,
    resolution                     TEXT NOT NULL,
    dataset_id                        TEXT NOT NULL REFERENCES datasets(dataset_id),
    run_type                             TEXT NOT NULL CHECK (run_type IN (
        'BACKTEST', 'WALK_FORWARD_FOLD', 'OOS', 'MONTE_CARLO'
    )),
    range_start_utc                          INTEGER NOT NULL,
    range_end_utc                               INTEGER NOT NULL,
    config_json                                    TEXT NOT NULL,
    trade_count                                       INTEGER NOT NULL,
    gross_pnl                                            REAL NOT NULL,
    net_pnl                                                 REAL NOT NULL,
    win_rate                                                   REAL,
    profit_factor                                                 REAL,
    avg_r                                                            REAL,
    max_drawdown                                                        REAL,
    total_cost                                                             REAL NOT NULL,
    metrics_json                                                              TEXT NOT NULL,
    origin                                                                       TEXT NOT NULL
);

CREATE INDEX idx_backtest_runs_symbol ON backtest_runs (canonical_symbol, run_type);

CREATE TABLE backtest_trades (
    id                  INTEGER PRIMARY KEY,
    run_id                TEXT NOT NULL REFERENCES backtest_runs(run_id),
    canonical_symbol         TEXT NOT NULL,
    strategy_key                 TEXT NOT NULL,
    direction                       TEXT NOT NULL CHECK (direction IN ('BUY', 'SELL')),
    entry_time_utc                     INTEGER NOT NULL,
    entry_price                           REAL NOT NULL,
    exit_time_utc                            INTEGER,
    exit_price                                  REAL,
    exit_reason                                    TEXT,
    volume                                            REAL NOT NULL,
    initial_monetary_risk                                REAL NOT NULL,
    realized_r                                              REAL,
    realized_pnl                                               REAL,
    total_cost                                                    REAL NOT NULL,
    entry_regime                                                     TEXT,
    exit_regime                                                         TEXT
);

CREATE INDEX idx_backtest_trades_run ON backtest_trades (run_id);
