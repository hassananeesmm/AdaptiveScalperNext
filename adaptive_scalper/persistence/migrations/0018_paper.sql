-- 0018_paper: PAPER mode state (directive section 132: "PAPER uses real
-- MT5 market data when available. No broker order_send. Clearly label
-- all resulting evidence: PAPER_LIVE_DATA.").
--
-- Deliberately SEPARATE tables from `positions`/`orders`/`deals` (real
-- broker evidence) -- directive section 82: "Evidence classes must not
-- silently receive identical weight." A simulated PAPER position must
-- never be reachable by `execution/reconciliation.py`'s broker-truth
-- recovery path, and a real broker position must never be contaminated
-- by simulated PAPER evidence.
--
-- `paper_session_state` is the resumable cursor an ongoing PAPER engine
-- (adaptive_scalper/paper/) needs across cycles: the currently-open
-- simulated position (if any), the running simulated equity, and the
-- latest bar time already processed (so the next cycle only decides on
-- genuinely NEW bars, never re-deciding already-processed ones).

-- `regime_confirmed`/`regime_candidate`/`regime_candidate_count` resume
-- the SAME hysteresis state `regimes.classifier.RegimeTracker` holds
-- mid-session -- without this, an incremental PAPER cycle would restart
-- the tracker from UNKNOWN every call, genuinely diverging from what a
-- continuously-running tracker would have decided (directive section
-- 13's "do not flip on one noisy bar" must hold across cycles too, not
-- just within one).
CREATE TABLE paper_session_state (
    id                          INTEGER PRIMARY KEY,
    session_key                   TEXT NOT NULL UNIQUE,
    canonical_symbol                 TEXT NOT NULL,
    resolution                          TEXT NOT NULL,
    equity                                 REAL NOT NULL,
    last_processed_bar_time_utc               INTEGER,
    open_position_json                           TEXT,
    regime_confirmed                                TEXT,
    regime_candidate                                   TEXT,
    regime_candidate_count                                INTEGER NOT NULL DEFAULT 0,
    created_at_utc                                           INTEGER NOT NULL,
    updated_at_utc                                              INTEGER NOT NULL
);

CREATE TABLE paper_trades (
    id                  INTEGER PRIMARY KEY,
    session_key           TEXT NOT NULL REFERENCES paper_session_state(session_key),
    canonical_symbol         TEXT NOT NULL,
    strategy_key                TEXT NOT NULL,
    direction                      TEXT NOT NULL CHECK (direction IN ('BUY', 'SELL')),
    entry_time_utc                    INTEGER NOT NULL,
    entry_price                          REAL NOT NULL,
    volume                                  REAL NOT NULL,
    initial_monetary_risk                      REAL NOT NULL,
    entry_regime                                  TEXT NOT NULL,
    exit_time_utc                                    INTEGER NOT NULL,
    exit_price                                          REAL NOT NULL,
    exit_reason                                            TEXT NOT NULL,
    exit_regime                                               TEXT,
    realized_r                                                   REAL,
    realized_pnl                                                    REAL NOT NULL,
    total_cost                                                         REAL NOT NULL,
    entry_features_json                                                   TEXT,
    entry_raw_confidence                                                     REAL,
    origin                                                                      TEXT NOT NULL DEFAULT 'PAPER_LIVE_DATA',
    recorded_at_utc                                                                INTEGER NOT NULL,
    -- A cycle retried after a crash re-derives the SAME deterministic
    -- trades from the SAME unprocessed bar window -- this constraint
    -- makes recording them idempotent (INSERT OR IGNORE) rather than
    -- risking duplicate rows on retry.
    UNIQUE (session_key, entry_time_utc, direction)
);

CREATE INDEX idx_paper_trades_session ON paper_trades (session_key, entry_time_utc);
