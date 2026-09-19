-- 0005_broker_account_history: import of the connected DEMO account's
-- broker order/deal history (directive sections 51-52). Deduplication key
-- is (login, server, ticket) — MT5 tickets are only unique within one
-- broker/server/account combination, so importing against a different
-- account later can't collide with an earlier one's history.
--
-- `type`/`state`/`entry` store MT5's raw ENUM_ORDER_TYPE/ENUM_ORDER_STATE/
-- ENUM_DEAL_ENTRY integer codes, undecoded — see
-- gateway/types.py's HistoricalOrder/HistoricalDeal docstrings for why.
--
-- `strategy_attribution` defaults to 'UNKNOWN', never inferred from
-- outcome (directive section 52) — nothing here may write 'MANUAL' or a
-- real strategy key without genuine provenance, which does not exist yet
-- (no decision journal to cross-reference against).

CREATE TABLE broker_account_orders (
    id                  INTEGER PRIMARY KEY,
    login               INTEGER NOT NULL,
    server              TEXT NOT NULL,
    ticket              INTEGER NOT NULL,
    time_setup_utc      INTEGER NOT NULL,
    time_done_utc       INTEGER,
    type                INTEGER NOT NULL,
    state               INTEGER NOT NULL,
    magic               INTEGER NOT NULL,
    position_id         INTEGER NOT NULL,
    volume_initial      REAL NOT NULL,
    volume_current      REAL NOT NULL,
    price_open          REAL NOT NULL,
    sl                  REAL NOT NULL,
    tp                  REAL NOT NULL,
    price_current       REAL NOT NULL,
    symbol              TEXT NOT NULL,
    comment             TEXT NOT NULL DEFAULT '',
    external_id         TEXT NOT NULL DEFAULT '',
    origin              TEXT NOT NULL DEFAULT 'BROKER_ACCOUNT_HISTORY',
    strategy_attribution TEXT NOT NULL DEFAULT 'UNKNOWN',
    imported_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (login, server, ticket)
);

CREATE INDEX idx_broker_orders_symbol_time ON broker_account_orders (symbol, time_setup_utc);

CREATE TABLE broker_account_deals (
    id                  INTEGER PRIMARY KEY,
    login               INTEGER NOT NULL,
    server              TEXT NOT NULL,
    ticket              INTEGER NOT NULL,
    order_ticket        INTEGER NOT NULL,
    time_utc            INTEGER NOT NULL,
    type                INTEGER NOT NULL,
    entry               INTEGER NOT NULL,
    magic               INTEGER NOT NULL,
    position_id         INTEGER NOT NULL,
    volume              REAL NOT NULL,
    price               REAL NOT NULL,
    commission          REAL NOT NULL,
    swap                REAL NOT NULL,
    profit              REAL NOT NULL,
    fee                 REAL NOT NULL,
    symbol              TEXT NOT NULL,
    comment             TEXT NOT NULL DEFAULT '',
    external_id         TEXT NOT NULL DEFAULT '',
    origin              TEXT NOT NULL DEFAULT 'BROKER_ACCOUNT_HISTORY',
    strategy_attribution TEXT NOT NULL DEFAULT 'UNKNOWN',
    imported_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (login, server, ticket)
);

CREATE INDEX idx_broker_deals_symbol_time ON broker_account_deals (symbol, time_utc);
