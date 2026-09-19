-- 0002_symbol_mapping: persisted canonical -> broker symbol resolution
-- (directive section 6). One row per canonical symbol; re-resolution
-- overwrites the row rather than accumulating history, since only the
-- current mapping is operationally meaningful.

CREATE TABLE symbol_mapping (
    canonical       TEXT PRIMARY KEY,
    broker_symbol   TEXT,
    resolved        INTEGER NOT NULL CHECK (resolved IN (0, 1)),
    reason          TEXT NOT NULL,
    resolved_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
