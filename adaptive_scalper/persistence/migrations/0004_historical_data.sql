-- 0004_historical_data: five-year MT5 historical bootstrap storage
-- (directive sections 46-50). Bars and ticks are raw broker history, kept
-- separate from the resumable job checkpoints that drive their download
-- and the derived coverage rows the dashboard reads (section 97).

-- One row per (symbol, resolution, bar-open-time). INSERT OR IGNORE against
-- this UNIQUE constraint is what makes re-importing an already-covered
-- range a no-op (directive section 49: "repeated import creates no
-- duplicates").
CREATE TABLE bars (
    id              INTEGER PRIMARY KEY,
    canonical_symbol TEXT NOT NULL,
    resolution      TEXT NOT NULL,
    ts_utc          INTEGER NOT NULL,
    open            REAL NOT NULL,
    high            REAL NOT NULL,
    low             REAL NOT NULL,
    close           REAL NOT NULL,
    tick_volume     INTEGER NOT NULL,
    spread          INTEGER NOT NULL,
    real_volume     INTEGER NOT NULL,
    imported_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (canonical_symbol, resolution, ts_utc)
);

CREATE INDEX idx_bars_symbol_resolution_ts ON bars (canonical_symbol, resolution, ts_utc);

-- One row per raw tick. Uniqueness is keyed on the millisecond timestamp
-- (MT5's time_msc), not the second-resolution `time` field, since two
-- genuinely distinct ticks routinely share the same second.
CREATE TABLE ticks (
    id              INTEGER PRIMARY KEY,
    canonical_symbol TEXT NOT NULL,
    ts_utc          INTEGER NOT NULL,
    ts_msc          INTEGER NOT NULL,
    bid             REAL NOT NULL,
    ask             REAL NOT NULL,
    last            REAL NOT NULL,
    volume          REAL NOT NULL,
    imported_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (canonical_symbol, ts_msc)
);

CREATE INDEX idx_ticks_symbol_ts ON ticks (canonical_symbol, ts_utc);

-- Resumable download checkpoints (directive section 49). `cursor_utc` is
-- the UTC second up to which this job has verified-imported data; a
-- restart resumes a PENDING/IN_PROGRESS job from `cursor_utc` rather than
-- `requested_start_utc`, so a full five-year history is never redownloaded
-- from scratch after an interruption.
--
-- `resolution` is '' (empty string), never NULL, for TICK jobs: SQLite's
-- UNIQUE constraint treats every NULL as distinct, which would silently
-- allow multiple TICK job rows for the same symbol.
CREATE TABLE historical_import_jobs (
    id                  INTEGER PRIMARY KEY,
    canonical_symbol    TEXT NOT NULL,
    data_kind           TEXT NOT NULL CHECK (data_kind IN ('BAR', 'TICK')),
    resolution          TEXT NOT NULL DEFAULT '',
    requested_start_utc INTEGER NOT NULL,
    requested_end_utc   INTEGER NOT NULL,
    cursor_utc          INTEGER NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ('PENDING', 'IN_PROGRESS', 'COMPLETE', 'FAILED')),
    last_error          TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (canonical_symbol, data_kind, resolution)
);

-- Derived, rebuildable-from-bars summary the dashboard historical-data
-- panel (directive section 97) reads directly rather than aggregating the
-- full bars table on every request. Refreshed after each import chunk.
CREATE TABLE historical_bar_coverage (
    canonical_symbol    TEXT NOT NULL,
    resolution          TEXT NOT NULL,
    earliest_utc        INTEGER,
    latest_utc          INTEGER,
    bar_count           INTEGER NOT NULL DEFAULT 0,
    expected_bar_count  INTEGER,
    gap_count           INTEGER NOT NULL DEFAULT 0,
    last_sync_at        TEXT,
    PRIMARY KEY (canonical_symbol, resolution)
);

CREATE TABLE historical_tick_coverage (
    canonical_symbol    TEXT PRIMARY KEY,
    earliest_utc        INTEGER,
    latest_utc          INTEGER,
    tick_count          INTEGER NOT NULL DEFAULT 0,
    last_sync_at        TEXT
);
