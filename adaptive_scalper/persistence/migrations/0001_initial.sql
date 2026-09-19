-- 0001_initial: migration tracking, persistent app state (incl. the kill
-- switch), and a configuration-change audit trail.
--
-- Kept deliberately minimal (directive section 53's full table list is
-- built out incrementally as each subsystem lands, not speculatively —
-- "no showpiece modules", section 118). Phase 1 needs exactly these three.

CREATE TABLE schema_migrations (
    version     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Single-row-per-key persistent state. Used for the kill switch
-- (directive section 37: must survive restart, never auto-clears) and
-- other small cross-cutting flags that aren't worth their own table.
CREATE TABLE app_state (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Append-only record of who/what changed a configuration value and when.
-- Never updated or deleted, mirroring the journal's append-only rule
-- (directive section 54).
CREATE TABLE configuration_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    changed_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    actor       TEXT NOT NULL,
    key         TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT NOT NULL,
    reason      TEXT NOT NULL
);
