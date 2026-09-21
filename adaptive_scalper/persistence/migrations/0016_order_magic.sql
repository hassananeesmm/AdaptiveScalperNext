-- 0016_order_magic: persists the order's own magic number (external
-- review finding #11) -- needed, alongside direction/requested-volume/
-- created_at (already present), to narrow UNKNOWN-without-broker-id
-- secondary correlation beyond a bare token+symbol match.

ALTER TABLE orders ADD COLUMN magic INTEGER NOT NULL DEFAULT 0;
