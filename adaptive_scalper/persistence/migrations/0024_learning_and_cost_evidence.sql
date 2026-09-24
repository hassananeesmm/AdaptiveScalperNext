-- 0024_learning_and_cost_evidence (completion directive Phases 6-7).
--
-- 1. backtest_trades.entry_features_json: the exact causal feature
--    snapshot each simulated entry was decided on, so the training job
--    can build datasets from persisted runs (paper_trades already has it).
ALTER TABLE backtest_trades ADD COLUMN entry_features_json TEXT;

-- 2. One row per DEMO entry order that reached the broker: what was
--    quoted, requested and estimated at decision time, what the broker
--    actually did (fill price, slippage, commission/fee, retcode, fill
--    type), market context (session, volatility, news proximity), and --
--    filled in later by the off-hot-path sweep once the position closes --
--    exit commission/fee and swap. Evidence for replacing
--    UNVERIFIED_ASSUMPTION cost inputs; it never changes config itself.
CREATE TABLE execution_cost_observations (
    id  INTEGER PRIMARY KEY,
    order_id  INTEGER NOT NULL UNIQUE REFERENCES orders(id),
    chain_key  TEXT,
    canonical_symbol  TEXT NOT NULL,
    broker_symbol  TEXT NOT NULL,
    direction  TEXT NOT NULL CHECK (direction IN ('BUY', 'SELL')),
    outcome_status  TEXT NOT NULL,
    decided_at_utc  INTEGER NOT NULL,
    quote_time  INTEGER,   -- broker tick time (server clock; BUG_BACKLOG #14)
    quote_time_msc  INTEGER,
    bid  REAL,
    ask  REAL,
    spread_price  REAL,
    requested_price  REAL,
    requested_volume  REAL NOT NULL,
    filled_volume  REAL,
    fill_price  REAL,   -- volume-weighted over IN deals
    slippage_price  REAL,   -- positive = adverse
    entry_commission  REAL,
    entry_fee  REAL,
    broker_retcode  INTEGER,
    broker_comment  TEXT,
    fill_type  TEXT,   -- FULL / PARTIAL / NONE
    deal_count  INTEGER,
    estimated_spread_price  REAL,
    estimated_commission_price  REAL,
    estimated_slippage_price  REAL,
    estimated_total_price  REAL,
    session  TEXT,
    hour_utc  INTEGER,
    atr  REAL,
    realized_volatility  REAL,
    spread_percentile  REAL,
    seconds_to_next_news  INTEGER,
    seconds_since_last_news  INTEGER,
    broker_position_id  TEXT,
    exit_commission  REAL,
    exit_fee  REAL,
    swap  REAL,
    exit_recorded_at_utc  INTEGER,
    recorded_at_utc  INTEGER NOT NULL
);
CREATE INDEX idx_execution_cost_observations_symbol ON execution_cost_observations (canonical_symbol, decided_at_utc);
CREATE INDEX idx_execution_cost_observations_open ON execution_cost_observations (broker_position_id) WHERE exit_recorded_at_utc IS NULL;
