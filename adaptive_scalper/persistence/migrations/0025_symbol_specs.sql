-- 0025_symbol_specs: the last broker SymbolSpec captured for each canonical
-- symbol (by `symbols`, `history bootstrap` and runtime startup on the
-- Windows laptop), so offline research commands (backtest, walk-forward,
-- oos, path-stress) can size and cost simulated trades without an MT5
-- connection. Contract/tick metadata only -- no account data.
CREATE TABLE symbol_specs (
    canonical_symbol  TEXT PRIMARY KEY,
    broker_symbol     TEXT NOT NULL,
    spec_json         TEXT NOT NULL,
    captured_at_utc   INTEGER NOT NULL
);
