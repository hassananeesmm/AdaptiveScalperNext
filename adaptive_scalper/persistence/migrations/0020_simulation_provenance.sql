-- 0020_simulation_provenance: per-component simulated costs, causal fill
-- references and provenance on every simulated trade; PAPER session
-- configuration fingerprint and resumable risk state; overlap-aware OOS
-- ledger purposes.

-- PAPER session immutability: a session may only be resumed under the
-- exact configuration that produced its state (paper.engine refuses a
-- mismatch). risk_state_json carries peak equity and the current UTC
-- day's realized P/L across cycles, so the daily-loss and drawdown
-- ceilings survive restarts.
ALTER TABLE paper_session_state ADD COLUMN config_fingerprint TEXT;
ALTER TABLE paper_session_state ADD COLUMN config_json TEXT;
ALTER TABLE paper_session_state ADD COLUMN risk_state_json TEXT;

ALTER TABLE paper_trades ADD COLUMN strategy_version INTEGER;
ALTER TABLE paper_trades ADD COLUMN signal_time_utc INTEGER;
ALTER TABLE paper_trades ADD COLUMN entry_fill_reference TEXT;
ALTER TABLE paper_trades ADD COLUMN exit_fill_reference TEXT;
ALTER TABLE paper_trades ADD COLUMN exit_decision_time_utc INTEGER;
ALTER TABLE paper_trades ADD COLUMN entry_spread_cost REAL;
ALTER TABLE paper_trades ADD COLUMN entry_slippage_cost REAL;
ALTER TABLE paper_trades ADD COLUMN exit_spread_cost REAL;
ALTER TABLE paper_trades ADD COLUMN exit_slippage_cost REAL;
ALTER TABLE paper_trades ADD COLUMN commission_cost REAL;
ALTER TABLE paper_trades ADD COLUMN swap_cost REAL;
ALTER TABLE paper_trades ADD COLUMN fee_cost REAL;
ALTER TABLE paper_trades ADD COLUMN gross_pnl REAL;
ALTER TABLE paper_trades ADD COLUMN peak_r REAL;
ALTER TABLE paper_trades ADD COLUMN fill_model_version TEXT;
ALTER TABLE paper_trades ADD COLUMN cost_provenance TEXT;
ALTER TABLE paper_trades ADD COLUMN config_fingerprint TEXT;
ALTER TABLE paper_trades ADD COLUMN evidence_json TEXT;

ALTER TABLE backtest_trades ADD COLUMN strategy_version INTEGER;
ALTER TABLE backtest_trades ADD COLUMN signal_time_utc INTEGER;
ALTER TABLE backtest_trades ADD COLUMN entry_fill_reference TEXT;
ALTER TABLE backtest_trades ADD COLUMN exit_fill_reference TEXT;
ALTER TABLE backtest_trades ADD COLUMN exit_decision_time_utc INTEGER;
ALTER TABLE backtest_trades ADD COLUMN entry_spread_cost REAL;
ALTER TABLE backtest_trades ADD COLUMN entry_slippage_cost REAL;
ALTER TABLE backtest_trades ADD COLUMN exit_spread_cost REAL;
ALTER TABLE backtest_trades ADD COLUMN exit_slippage_cost REAL;
ALTER TABLE backtest_trades ADD COLUMN commission_cost REAL;
ALTER TABLE backtest_trades ADD COLUMN swap_cost REAL;
ALTER TABLE backtest_trades ADD COLUMN fee_cost REAL;
ALTER TABLE backtest_trades ADD COLUMN gross_pnl REAL;
ALTER TABLE backtest_trades ADD COLUMN peak_r REAL;
ALTER TABLE backtest_trades ADD COLUMN fill_model_version TEXT;
ALTER TABLE backtest_trades ADD COLUMN cost_provenance TEXT;
ALTER TABLE backtest_trades ADD COLUMN config_fingerprint TEXT;
ALTER TABLE backtest_trades ADD COLUMN evidence_json TEXT;

ALTER TABLE backtest_runs ADD COLUMN config_fingerprint TEXT;
ALTER TABLE backtest_runs ADD COLUMN fill_model_version TEXT;
ALTER TABLE backtest_runs ADD COLUMN cost_provenance TEXT;

-- dataset_usage gains purposes for the research validation layer and an
-- explicit analysis-only OOS reuse label (a deliberate, recorded
-- exception, never a silent second "untouched" OOS). SQLite cannot alter
-- a CHECK constraint, so the table is rebuilt; nothing references it.
CREATE TABLE dataset_usage_new (
    id                  INTEGER PRIMARY KEY,
    dataset_id            TEXT NOT NULL REFERENCES datasets(dataset_id),
    used_by_run_id           TEXT NOT NULL,
    used_for                    TEXT NOT NULL CHECK (used_for IN (
        'TRAINING', 'VALIDATION', 'OOS', 'WALK_FORWARD_FOLD', 'MONTE_CARLO',
        'OOS_ANALYSIS_REUSE', 'MODEL_WALK_FORWARD', 'PURGED_CV', 'PATH_STRESS'
    )),
    used_at_utc                     INTEGER NOT NULL
);
INSERT INTO dataset_usage_new (id, dataset_id, used_by_run_id, used_for, used_at_utc)
    SELECT id, dataset_id, used_by_run_id, used_for, used_at_utc FROM dataset_usage;
DROP TABLE dataset_usage;
ALTER TABLE dataset_usage_new RENAME TO dataset_usage;
CREATE INDEX idx_dataset_usage_dataset ON dataset_usage (dataset_id);
CREATE INDEX idx_datasets_range ON datasets (canonical_symbol, range_start_utc, range_end_utc);
