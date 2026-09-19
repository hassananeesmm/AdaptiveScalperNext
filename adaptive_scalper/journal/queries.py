"""Read-side queries for the decision journal."""

from __future__ import annotations

import json
import sqlite3

from adaptive_scalper.journal.events import JournalEvent


def _row_to_event(row: sqlite3.Row, chain_key: str) -> JournalEvent:
    return JournalEvent(
        id=row["id"],
        chain_id=row["chain_id"],
        chain_key=chain_key,
        sequence_in_chain=row["sequence_in_chain"],
        event_type=row["event_type"],
        event_timestamp_utc=row["event_timestamp_utc"],
        recorded_at=row["recorded_at"],
        canonical_symbol=row["canonical_symbol"],
        broker_symbol=row["broker_symbol"],
        strategy_key=row["strategy_key"],
        client_request_id=row["client_request_id"],
        broker_order_id=row["broker_order_id"],
        broker_position_id=row["broker_position_id"],
        broker_deal_id=row["broker_deal_id"],
        payload=json.loads(row["payload_json"]),
    )


def get_chain_events(conn: sqlite3.Connection, chain_key: str) -> list[JournalEvent]:
    """All events for one chain, in deterministic chain order — the
    directive section 56 "complete causal decision chain", traceable
    from the first event to the last. Returns an empty list (not an
    error) for an unknown chain_key."""
    chain_row = conn.execute("SELECT id FROM decision_chains WHERE chain_key = ?", (chain_key,)).fetchone()
    if chain_row is None:
        return []
    rows = conn.execute(
        "SELECT * FROM journal_events WHERE chain_id = ? ORDER BY sequence_in_chain",
        (chain_row["id"],),
    ).fetchall()
    return [_row_to_event(r, chain_key) for r in rows]


def get_events_by_type(
    conn: sqlite3.Connection,
    event_type: str,
    *,
    since_utc: int | None = None,
    limit: int = 100,
) -> list[JournalEvent]:
    """Most recent events of one type, newest first, optionally bounded
    to `event_timestamp_utc >= since_utc`."""
    query = """
        SELECT je.*, dc.chain_key AS chain_key
        FROM journal_events je
        JOIN decision_chains dc ON dc.id = je.chain_id
        WHERE je.event_type = ?
    """
    params: list = [event_type]
    if since_utc is not None:
        query += " AND je.event_timestamp_utc >= ?"
        params.append(since_utc)
    query += " ORDER BY je.event_timestamp_utc DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return [_row_to_event(r, r["chain_key"]) for r in rows]


def get_events_for_broker_order(conn: sqlite3.Connection, broker_order_id: str) -> list[JournalEvent]:
    """Every event referencing one broker order id, across whatever
    chain(s) it appears in, in chronological order — the practical query
    reconciliation needs: "what does the journal know about this order?"."""
    rows = conn.execute(
        """
        SELECT je.*, dc.chain_key AS chain_key
        FROM journal_events je
        JOIN decision_chains dc ON dc.id = je.chain_id
        WHERE je.broker_order_id = ?
        ORDER BY je.event_timestamp_utc
        """,
        (broker_order_id,),
    ).fetchall()
    return [_row_to_event(r, r["chain_key"]) for r in rows]
