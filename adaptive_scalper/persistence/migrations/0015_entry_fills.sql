-- 0015_entry_fills: durable typed pending/filled/remaining risk
-- accounting on orders (external review findings #5/#6), and richer
-- broker deal fidelity on deals -- fee, entry/deal type, broker order
-- ticket, magic, comment (findings #9/#10). Backwards-safe: every new
-- column is nullable or has a safe default; no existing row's meaning
-- changes.

ALTER TABLE orders ADD COLUMN requested_monetary_risk REAL;
ALTER TABLE orders ADD COLUMN filled_volume REAL NOT NULL DEFAULT 0.0;
ALTER TABLE orders ADD COLUMN filled_initial_monetary_risk REAL NOT NULL DEFAULT 0.0;
ALTER TABLE orders ADD COLUMN remaining_volume REAL;
ALTER TABLE orders ADD COLUMN remaining_pending_monetary_risk REAL;

ALTER TABLE deals ADD COLUMN fee REAL NOT NULL DEFAULT 0.0;
-- entry_type: MT5 ENUM_DEAL_ENTRY as text ('IN'/'OUT'/'INOUT'/'OUT_BY'),
-- nullable for pre-existing rows written before this migration existed.
ALTER TABLE deals ADD COLUMN entry_type TEXT;
ALTER TABLE deals ADD COLUMN deal_type TEXT;
ALTER TABLE deals ADD COLUMN broker_order_ticket TEXT;
ALTER TABLE deals ADD COLUMN magic INTEGER;
ALTER TABLE deals ADD COLUMN comment TEXT;
