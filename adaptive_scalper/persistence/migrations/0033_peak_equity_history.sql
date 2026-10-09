-- 0033_peak_equity_history: account-bound, append-only drawdown baseline (ASN-034).
--
-- Before this migration the DEMO drawdown baseline was one upserted
-- runtime_state row ("peak_equity"): not bound to any broker account, no
-- history, and only written when the global entry block happened to reach
-- the drawdown step. Every change of the baseline is now one INSERT here;
-- the current peak of an account is its newest row. Nothing may UPDATE or
-- DELETE a row, so a reset can lower the baseline only by appending an
-- audited OPERATOR_RESET row that keeps everything before it.
--
--   INITIALIZED     first observation on a database with no baseline at all
--   LEGACY_ADOPTED  the pre-0033 unbound runtime_state value, adopted once for
--                   the first account observed after this migration
--   RAISED          broker equity exceeded the current peak
--   OPERATOR_RESET  `peak-equity reset` (kill switch ENGAGED, reason, evidence)

CREATE TABLE peak_equity_history (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    account_login          INTEGER NOT NULL,
    account_server         TEXT NOT NULL CHECK (length(account_server) > 0),
    event_type             TEXT NOT NULL CHECK (event_type IN
                               ('INITIALIZED', 'LEGACY_ADOPTED', 'RAISED', 'OPERATOR_RESET')),
    peak_equity            REAL NOT NULL CHECK (peak_equity > 0),
    previous_peak_equity   REAL,
    observed_equity        REAL,
    actor                  TEXT NOT NULL CHECK (length(actor) > 0),
    reason                 TEXT NOT NULL CHECK (length(reason) > 0),
    evidence_path          TEXT,
    evidence_sha256        TEXT,
    recorded_at_utc        INTEGER NOT NULL,
    CHECK (event_type != 'OPERATOR_RESET' OR (evidence_sha256 IS NOT NULL AND length(evidence_sha256) = 64))
);

CREATE INDEX idx_peak_equity_history_account ON peak_equity_history (account_login, account_server, id);

CREATE TRIGGER trg_peak_equity_history_no_update
BEFORE UPDATE ON peak_equity_history
BEGIN
    SELECT RAISE(ABORT, 'peak_equity_history is append-only: UPDATE is not permitted');
END;

CREATE TRIGGER trg_peak_equity_history_no_delete
BEFORE DELETE ON peak_equity_history
BEGIN
    SELECT RAISE(ABORT, 'peak_equity_history is append-only: DELETE is not permitted');
END;
