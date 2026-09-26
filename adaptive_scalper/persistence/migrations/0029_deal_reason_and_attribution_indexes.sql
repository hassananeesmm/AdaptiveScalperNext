-- Strategy Lab attribution (directive: FINAL ENGINEERING, sections 8-9).
--
-- 1. The broker's own DEAL_REASON (MT5 ENUM_DEAL_REASON: CLIENT, MOBILE,
--    WEB, EXPERT, SL, TP, SO, ...) for imported account-history deals. It
--    is the authoritative evidence for "manual", "broker stop-loss/take-
--    profit" and "expert" activity. NULL = not recorded (every row imported
--    before this migration); never back-filled or guessed.
-- 2. Lookup indexes for the read-only Strategy Lab ledger (per-position deal
--    grouping, per-account time windows, per-strategy journal evidence).
-- Purely additive: no existing row is changed or deleted.
ALTER TABLE broker_account_deals ADD COLUMN reason INTEGER;

CREATE INDEX IF NOT EXISTS idx_broker_deals_position ON broker_account_deals (position_id);
CREATE INDEX IF NOT EXISTS idx_broker_deals_login_time ON broker_account_deals (login, time_utc);
CREATE INDEX IF NOT EXISTS idx_journal_events_strategy_type ON journal_events (strategy_key, event_type, event_timestamp_utc);
