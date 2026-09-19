"""Broker account order/deal history import (directive sections 51-52).

Distinct from bar/tick market-data bootstrap: this imports the CONNECTED
ACCOUNT's own trading history (any origin — this bot, a human, another
EA), not per-symbol market data. Idempotent by (login, server, ticket);
never attributes an imported trade to a strategy without genuine
provenance, which nothing in this codebase can currently establish (no
decision journal exists yet to cross-reference against) — every import is
labeled `strategy_attribution = 'UNKNOWN'`.
"""

from __future__ import annotations

import sqlite3

from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.gateway.types import AccountSnapshot, HistoricalDeal, HistoricalOrder

ORIGIN = "BROKER_ACCOUNT_HISTORY"
UNKNOWN_STRATEGY = "UNKNOWN"


def import_orders(
    conn: sqlite3.Connection, account: AccountSnapshot, orders: list[HistoricalOrder]
) -> int:
    """Insert orders, skipping any already present for this
    (login, server, ticket). Returns the count actually inserted."""
    if not orders:
        return 0
    before = conn.total_changes
    conn.executemany(
        """
        INSERT OR IGNORE INTO broker_account_orders
            (login, server, ticket, time_setup_utc, time_done_utc, type, state,
             magic, position_id, volume_initial, volume_current, price_open,
             sl, tp, price_current, symbol, comment, external_id,
             origin, strategy_attribution)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                account.login, account.server, o.ticket, o.time_setup, o.time_done,
                o.type, o.state, o.magic, o.position_id, o.volume_initial, o.volume_current,
                o.price_open, o.sl, o.tp, o.price_current, o.symbol, o.comment, o.external_id,
                ORIGIN, UNKNOWN_STRATEGY,
            )
            for o in orders
        ],
    )
    return conn.total_changes - before


def import_deals(
    conn: sqlite3.Connection, account: AccountSnapshot, deals: list[HistoricalDeal]
) -> int:
    """Insert deals, skipping any already present for this
    (login, server, ticket). Returns the count actually inserted."""
    if not deals:
        return 0
    before = conn.total_changes
    conn.executemany(
        """
        INSERT OR IGNORE INTO broker_account_deals
            (login, server, ticket, order_ticket, time_utc, type, entry, magic,
             position_id, volume, price, commission, swap, profit, fee, symbol,
             comment, external_id, origin, strategy_attribution)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                account.login, account.server, d.ticket, d.order, d.time, d.type, d.entry,
                d.magic, d.position_id, d.volume, d.price, d.commission, d.swap, d.profit,
                d.fee, d.symbol, d.comment, d.external_id, ORIGIN, UNKNOWN_STRATEGY,
            )
            for d in deals
        ],
    )
    return conn.total_changes - before


def import_account_history(
    conn: sqlite3.Connection,
    gateway: Gateway,
    account: AccountSnapshot,
    date_from_utc: int,
    date_to_utc: int,
) -> tuple[int, int]:
    """Fetch and idempotently store the connected account's order and deal
    history over [date_from_utc, date_to_utc]. Returns
    (orders_inserted, deals_inserted) — the counts of genuinely NEW rows,
    not the total fetched, so a repeated call over an overlapping range
    correctly reports 0 on already-imported history."""
    orders = gateway.history_orders_get(date_from_utc, date_to_utc)
    deals = gateway.history_deals_get(date_from_utc, date_to_utc)
    return (
        import_orders(conn, account, orders),
        import_deals(conn, account, deals),
    )


def coverage(conn: sqlite3.Connection, login: int, server: str) -> dict:
    """Earliest/latest/count for this account's imported orders and deals —
    directive section 98's broker-history dashboard panel's data source
    (no dashboard panel exists yet; this is queried directly for now)."""
    orders_row = conn.execute(
        """
        SELECT MIN(time_setup_utc) AS earliest, MAX(time_setup_utc) AS latest, COUNT(*) AS n
        FROM broker_account_orders WHERE login = ? AND server = ?
        """,
        (login, server),
    ).fetchone()
    deals_row = conn.execute(
        """
        SELECT MIN(time_utc) AS earliest, MAX(time_utc) AS latest, COUNT(*) AS n
        FROM broker_account_deals WHERE login = ? AND server = ?
        """,
        (login, server),
    ).fetchone()
    return {
        "orders": {"earliest_utc": orders_row["earliest"], "latest_utc": orders_row["latest"], "count": orders_row["n"]},
        "deals": {"earliest_utc": deals_row["earliest"], "latest_utc": deals_row["latest"], "count": deals_row["n"]},
    }
