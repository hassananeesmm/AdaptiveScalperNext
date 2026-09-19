-- 0008_costs: track estimated vs. realized transaction cost (directive
-- section 34: "Track estimated versus realized costs"). Realized fields
-- are nullable and filled in later, once real fills exist (the
-- execution state machine this project doesn't have yet) — a row is
-- created at decision time with only the estimate, and updated once
-- (never re-estimated) when the realized outcome is known.

CREATE TABLE cost_observations (
    id                          INTEGER PRIMARY KEY,
    canonical_symbol            TEXT NOT NULL,
    chain_key                   TEXT,   -- optional link to journal decision_chains.chain_key
    estimated_spread_cost       REAL NOT NULL,
    estimated_commission_cost   REAL NOT NULL,
    estimated_slippage_cost     REAL NOT NULL,
    estimated_swap_cost         REAL NOT NULL,
    estimated_uncertainty_margin REAL NOT NULL,
    estimated_total_cost        REAL NOT NULL,
    realized_spread_cost        REAL,
    realized_commission_cost    REAL,
    realized_slippage_cost      REAL,
    realized_swap_cost          REAL,
    realized_total_cost         REAL,
    prediction_error            REAL,   -- realized_total_cost - estimated_total_cost
    recorded_at_utc             INTEGER NOT NULL,
    realized_at_utc             INTEGER
);

CREATE INDEX idx_cost_observations_symbol ON cost_observations (canonical_symbol, recorded_at_utc);
CREATE INDEX idx_cost_observations_chain ON cost_observations (chain_key);
