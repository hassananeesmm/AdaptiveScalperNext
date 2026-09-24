-- 0026_order_check_probes: evidence from `adaptive-scalper order-check-probe`
-- (completion directive Phase 16) -- one real, NEVER-SENT DEMO order_check()
-- per row, recorded to settle the success-retcode convention (BUG_BACKLOG #5)
-- with broker evidence instead of an assumption. No account identifiers.
CREATE TABLE order_check_probes (
    id  INTEGER PRIMARY KEY,
    checked_at_utc  INTEGER NOT NULL,
    canonical_symbol  TEXT NOT NULL,
    broker_symbol  TEXT,
    direction  TEXT NOT NULL,
    volume  REAL,
    status  TEXT NOT NULL,   -- CHECKED / BLOCKED / ERROR
    stage  TEXT NOT NULL,
    retcode  INTEGER,
    comment  TEXT,
    margin_required  REAL,
    broker_company  TEXT,
    broker_server  TEXT,
    terminal_build  INTEGER,
    detail  TEXT NOT NULL
);
