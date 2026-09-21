-- 0017_incident_dedup: stable incident identity (external review finding
-- #15, 2026-09-21) -- repeated periodic reconciliation of the SAME
-- unresolved mismatch must not create an unlimited duplicate row every
-- cycle. `dedup_key` identifies "this specific ongoing problem";
-- first_seen/last_seen/occurrence_count preserve full history without
-- spamming new rows.

ALTER TABLE execution_incidents ADD COLUMN dedup_key TEXT;
ALTER TABLE execution_incidents ADD COLUMN first_seen_at_utc INTEGER;
ALTER TABLE execution_incidents ADD COLUMN last_seen_at_utc INTEGER;
ALTER TABLE execution_incidents ADD COLUMN occurrence_count INTEGER NOT NULL DEFAULT 1;

CREATE INDEX idx_execution_incidents_dedup ON execution_incidents (dedup_key, resolved_at_utc);
