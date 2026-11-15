CREATE TABLE project_drawdown_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL CHECK (event_type IN ('BLOCKED', 'OBSERVED', 'OPERATOR_RECOVERY')),
    account_login INTEGER,
    account_server TEXT,
    account_currency TEXT,
    magic INTEGER,
    latched INTEGER NOT NULL CHECK (latched IN (0, 1)),
    reason TEXT NOT NULL,
    actor TEXT NOT NULL,
    recorded_at_utc INTEGER NOT NULL,
    state_json TEXT NOT NULL,
    evidence_sha256 TEXT,
    CHECK (latched = 1 OR event_type IN ('OBSERVED', 'OPERATOR_RECOVERY')),
    CHECK (event_type != 'OPERATOR_RECOVERY' OR length(evidence_sha256) = 64)
);

INSERT INTO project_drawdown_events
    (event_type, latched, reason, actor, recorded_at_utc, state_json)
VALUES ('BLOCKED', 1,
    'Prior reported 5.19% breach: BLOCKED pending documented human accounting policy; no historical peak inferred',
    'migration:0034', 0, '{}');

CREATE TRIGGER project_drawdown_no_update BEFORE UPDATE ON project_drawdown_events
BEGIN
    SELECT RAISE(ABORT, 'project drawdown audit is append-only');
END;
CREATE TRIGGER project_drawdown_no_delete BEFORE DELETE ON project_drawdown_events
BEGIN
    SELECT RAISE(ABORT, 'project drawdown audit is append-only');
END;

CREATE TABLE project_drawdown_deals (
    ticket TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64)
);
CREATE TRIGGER project_drawdown_deals_no_update BEFORE UPDATE ON project_drawdown_deals
BEGIN
    SELECT RAISE(ABORT, 'project drawdown deal evidence is append-only');
END;
CREATE TRIGGER project_drawdown_deals_no_delete BEFORE DELETE ON project_drawdown_deals
BEGIN
    SELECT RAISE(ABORT, 'project drawdown deal evidence is append-only');
END;
