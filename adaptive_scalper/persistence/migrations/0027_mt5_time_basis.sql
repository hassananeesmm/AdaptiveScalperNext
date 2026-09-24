-- Time basis of the MT5-derived rows (BUG_BACKLOG #14).
--
-- Before schema 27 `Mt5Gateway` stored the broker SERVER clock in columns
-- named *_utc. A database that already holds MT5-derived rows is therefore
-- marked SERVER_UNCONVERTED; history sync, research and the runtime refuse
-- to use it until the operator runs `history convert-server-time`, which
-- backs the database up and converts those rows once. A database with no
-- such rows starts as UTC (the gateway converts from schema 27 on).
CREATE TABLE mt5_time_basis (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    basis            TEXT NOT NULL CHECK (basis IN ('UTC', 'SERVER_UNCONVERTED')),
    rule             TEXT,
    converted_at_utc INTEGER,
    detail           TEXT
);

INSERT INTO mt5_time_basis (id, basis, detail)
SELECT 1,
       CASE WHEN EXISTS (SELECT 1 FROM bars)
              OR EXISTS (SELECT 1 FROM ticks)
              OR EXISTS (SELECT 1 FROM broker_account_orders)
              OR EXISTS (SELECT 1 FROM broker_account_deals)
            THEN 'SERVER_UNCONVERTED' ELSE 'UTC' END,
       'set by migration 27';
