-- Rows whose broker server time falls in the hour the server clock skips at
-- the spring DST change (BUG_BACKLOG #14). No UTC instant produces such a
-- time, so `history convert-server-time` moves them here, with their
-- original server-clock values, instead of guessing a time or deleting them.
CREATE TABLE bars_unconvertible (
    id               INTEGER PRIMARY KEY,
    original_id      INTEGER NOT NULL,
    canonical_symbol TEXT NOT NULL,
    resolution       TEXT NOT NULL,
    server_ts        INTEGER NOT NULL,
    open             REAL NOT NULL,
    high             REAL NOT NULL,
    low              REAL NOT NULL,
    close            REAL NOT NULL,
    tick_volume      INTEGER,
    spread           INTEGER,
    real_volume      INTEGER,
    rule             TEXT NOT NULL,
    reason           TEXT NOT NULL,
    moved_at_utc     INTEGER NOT NULL
);

CREATE TABLE ticks_unconvertible (
    id               INTEGER PRIMARY KEY,
    original_id      INTEGER NOT NULL,
    canonical_symbol TEXT NOT NULL,
    server_ts        INTEGER NOT NULL,
    server_ts_msc    INTEGER NOT NULL,
    bid              REAL NOT NULL,
    ask              REAL NOT NULL,
    last             REAL NOT NULL,
    volume           REAL NOT NULL,
    rule             TEXT NOT NULL,
    reason           TEXT NOT NULL,
    moved_at_utc     INTEGER NOT NULL
);
