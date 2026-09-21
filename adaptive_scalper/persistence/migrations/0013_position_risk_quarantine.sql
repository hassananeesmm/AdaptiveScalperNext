-- 0013_position_risk_quarantine: external review finding #6 — an
-- unprovable/corrupted initial_monetary_risk must not be indistinguishable
-- from a normal, healthy HOLD. This table records the degraded-health
-- incident so it is queryable/persisted (directive section 75: technical
-- health blocks trading independent of financial loss), separate from
-- execution_incidents (whose incident_type CHECK constraint is scoped to
-- broker-reconciliation findings, a genuinely different concern).

CREATE TABLE position_risk_incidents (
    id                  INTEGER PRIMARY KEY,
    position_id          INTEGER NOT NULL REFERENCES positions(id),
    incident_type          TEXT NOT NULL CHECK (incident_type IN ('INVALID_INITIAL_RISK')),
    detail                    TEXT NOT NULL,
    detected_at_utc             INTEGER NOT NULL,
    resolved_at_utc               INTEGER,
    resolution                      TEXT
);

CREATE INDEX idx_position_risk_incidents_unresolved ON position_risk_incidents (resolved_at_utc);
CREATE INDEX idx_position_risk_incidents_position ON position_risk_incidents (position_id);
