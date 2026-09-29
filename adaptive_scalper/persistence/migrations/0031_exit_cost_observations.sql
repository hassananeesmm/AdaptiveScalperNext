-- Exit-side execution cost evidence (research finding 2026-09-29: the
-- backtest's per-fill slippage assumption dominates modelled cost, and only
-- the ENTRY side was ever observed in DEMO).
--
-- 1. close_requests: the executable quote the close was decided on, taken
--    from the SAME fresh round-2 tick the close path already fetches (no
--    extra broker call). NULL for every request written before this
--    migration; never back-filled.
-- 2. deals.reason: the broker's own MT5 ENUM_DEAL_REASON (CLIENT, MOBILE,
--    WEB, EXPERT, SL, TP, SO, ...) for deals the runtime records. NULL =
--    not recorded (pre-migration rows); never guessed.
-- 3. exit_cost_observations: one row per CLOSED position, written by the
--    off-hot-path cost sweep from broker evidence already in the DB: how
--    the position was closed, the reference price the exit should have
--    filled at, the volume-weighted exit fill, and the adverse slippage
--    (positive = worse for the trader). A reference that cannot be proven
--    from evidence stays NULL, and so does the slippage. Evidence only: it
--    never changes configuration.
-- Purely additive: no existing row is changed or deleted.
ALTER TABLE close_requests ADD COLUMN quote_bid REAL;
ALTER TABLE close_requests ADD COLUMN quote_ask REAL;
ALTER TABLE close_requests ADD COLUMN quote_time_msc INTEGER;

ALTER TABLE deals ADD COLUMN reason INTEGER;

CREATE TABLE exit_cost_observations (
    id                      INTEGER PRIMARY KEY,
    broker_position_id      TEXT NOT NULL UNIQUE,
    position_id             INTEGER REFERENCES positions(id),
    canonical_symbol        TEXT NOT NULL,
    position_direction      TEXT NOT NULL CHECK (position_direction IN ('BUY', 'SELL')),
    exit_kind               TEXT NOT NULL CHECK (exit_kind IN (
        'AGENT_CLOSE', 'STOP_LOSS', 'TAKE_PROFIT', 'MANUAL', 'STOP_OUT', 'OTHER', 'UNKNOWN'
    )),
    deal_reason             INTEGER,
    close_request_id        INTEGER REFERENCES close_requests(id),
    reference_price         REAL,
    reference_source        TEXT,   -- CLOSE_QUOTE / DEAL_COMMENT_TRIGGER / ENTRY_ORDER_LEVEL / NONE
    quote_spread_price      REAL,
    exit_fill_price         REAL,   -- volume-weighted over the position's non-IN deals
    exit_volume             REAL,
    exit_deal_count         INTEGER,
    exit_slippage_price     REAL,   -- positive = adverse; NULL when the reference is unproven
    estimated_slippage_price REAL,  -- the per-fill assumption the entry was costed with, if observed
    exit_time_utc           INTEGER,
    recorded_at_utc         INTEGER NOT NULL
);
CREATE INDEX idx_exit_cost_observations_symbol ON exit_cost_observations (canonical_symbol, exit_kind);
