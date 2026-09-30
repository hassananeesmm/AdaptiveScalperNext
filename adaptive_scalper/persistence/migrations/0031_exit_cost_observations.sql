-- Exit-side execution cost evidence, one row per ECONOMIC EXIT EVENT
-- (research finding 2026-09-29: the backtest's per-fill slippage assumption
-- dominates modelled cost, and only the ENTRY side was ever observed in DEMO).
--
-- 1. close_requests: the executable quote the close was decided on, taken
--    from the SAME fresh round-2 tick the close path already fetches (no
--    extra broker call), and the broker order ticket the send returned (the
--    proof that links a closing deal to THIS close instruction). NULL for
--    every request written before this migration; never back-filled.
-- 2. deals.reason: the broker's own MT5 ENUM_DEAL_REASON (CLIENT, MOBILE,
--    WEB, EXPERT, SL, TP, SO, ...) for deals the runtime records. NULL = not
--    recorded (pre-migration rows); never guessed.
-- 3. exit_cost_observations: one row per economic exit event = one broker
--    ORDER that closed (part of) a position. Several fills of the same order
--    are volume-weighted into that one event; separate orders on the same
--    position (a partial agent close, later the stop loss for the rest) are
--    separate events. Written by the off-hot-path cost sweep from evidence
--    already in the DB (local deals + imported account history). A reference
--    that cannot be proven stays NULL, and so does the slippage.
--    Lifecycle: PROVISIONAL rows are recomputed deterministically as late
--    deals arrive (version increments only when the content changes); a row
--    becomes FINAL only once the position is closed and its closing volume
--    equals its entry volume (or, after the settle horizon, FINAL with
--    exclusion_reason UNSETTLED_VOLUME, never counted). FINAL rows are
--    immutable (triggers below). INOUT (netting reversal), OUT_BY and
--    conflicting reasons are recorded but excluded from statistics.
--    Evidence only: nothing here changes configuration.
-- Purely additive: no existing row is changed or deleted.
ALTER TABLE close_requests ADD COLUMN quote_bid REAL;
ALTER TABLE close_requests ADD COLUMN quote_ask REAL;
ALTER TABLE close_requests ADD COLUMN quote_time_msc INTEGER;
ALTER TABLE close_requests ADD COLUMN broker_order_ticket TEXT;

ALTER TABLE deals ADD COLUMN reason INTEGER;

CREATE TABLE exit_cost_observations (
    id                       INTEGER PRIMARY KEY,
    event_key                TEXT NOT NULL UNIQUE,    -- <broker_position_id>:ORDER:<ticket> | <pid>:DEAL:<ticket>
    broker_position_id       TEXT NOT NULL,
    position_id              INTEGER REFERENCES positions(id),
    canonical_symbol         TEXT NOT NULL,
    position_direction       TEXT NOT NULL CHECK (position_direction IN ('BUY', 'SELL')),
    exit_kind                TEXT NOT NULL CHECK (exit_kind IN (
        'AGENT_CLOSE', 'STOP_LOSS', 'TAKE_PROFIT', 'MANUAL', 'STOP_OUT', 'OTHER', 'UNKNOWN'
    )),
    deal_reason              INTEGER,                 -- NULL when absent or conflicting inside the event
    broker_order_ticket      TEXT,
    deal_tickets             TEXT NOT NULL,           -- JSON array, sorted
    deal_count               INTEGER NOT NULL CHECK (deal_count > 0),
    entry_types              TEXT NOT NULL,           -- sorted, comma-separated: OUT / INOUT / OUT_BY
    close_request_id         INTEGER REFERENCES close_requests(id),
    reference_price          REAL,
    reference_source         TEXT NOT NULL CHECK (reference_source IN ('CLOSE_QUOTE', 'DEAL_COMMENT_TRIGGER', 'NONE')),
    quote_spread_price       REAL,
    exit_fill_price          REAL NOT NULL,           -- volume-weighted over THIS event's deals only
    exit_volume              REAL NOT NULL CHECK (exit_volume > 0),
    exit_slippage_price      REAL,                    -- positive = adverse; NULL when the reference is unproven
    estimated_slippage_price REAL,                    -- per-fill assumption the entry was costed with, if observed
    first_deal_time_utc      INTEGER NOT NULL,
    last_deal_time_utc       INTEGER NOT NULL,
    status                   TEXT NOT NULL CHECK (status IN ('PROVISIONAL', 'FINAL')),
    exclusion_reason         TEXT CHECK (exclusion_reason IS NULL OR exclusion_reason IN (
        'INOUT', 'OUT_BY', 'REASON_CONFLICT', 'UNSETTLED_VOLUME'
    )),
    version                  INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    first_recorded_at_utc    INTEGER NOT NULL,
    updated_at_utc           INTEGER NOT NULL,
    finalized_at_utc         INTEGER,
    CHECK ((status = 'FINAL') = (finalized_at_utc IS NOT NULL)),
    CHECK (exit_slippage_price IS NULL OR reference_price IS NOT NULL)
);
CREATE INDEX idx_exit_cost_observations_symbol ON exit_cost_observations (canonical_symbol, exit_kind, status);
CREATE INDEX idx_exit_cost_observations_position ON exit_cost_observations (broker_position_id);

CREATE TRIGGER exit_cost_observations_final_immutable
BEFORE UPDATE ON exit_cost_observations
WHEN OLD.status = 'FINAL'
BEGIN
    SELECT RAISE(ABORT, 'exit_cost_observations: FINAL rows are immutable');
END;

CREATE TRIGGER exit_cost_observations_final_undeletable
BEFORE DELETE ON exit_cost_observations
WHEN OLD.status = 'FINAL'
BEGIN
    SELECT RAISE(ABORT, 'exit_cost_observations: FINAL rows cannot be deleted');
END;
