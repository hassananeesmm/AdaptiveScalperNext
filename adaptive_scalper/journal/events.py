"""Append API for the immutable decision journal (directive sections
54-58).

Every event a decision chain produces — from SIGNAL_CREATED through
LEARNING_UPDATE — is appended here via `append_event()`. There is no
update/delete function in this module; the database itself enforces
that too (migration 0006's `trg_journal_events_no_update`/
`trg_journal_events_no_delete` triggers), as defense in depth against a
future bug in this module accidentally exposing a mutation path.
Recording a later outcome (a fill, a close, a learning update) always
means calling `append_event()` again with a NEW event — never editing an
earlier one, even when the earlier one's hypothesis turned out wrong.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

# Must match migration 0006's journal_events.event_type CHECK constraint
# exactly. Checked here too (not just left to the database) so a typo
# raises a clear, specific UnknownEventTypeError instead of a generic
# sqlite3.IntegrityError from the CHECK constraint.
EVENT_TYPES = frozenset({
    "SIGNAL_CREATED", "SIGNAL_REJECTED",
    "PROPOSAL_CREATED", "PROPOSAL_REJECTED",
    "ENTRY_ALLOWED", "ENTRY_BLOCKED",
    "ORDER_SUBMITTED", "ORDER_ACCEPTED", "ORDER_PENDING", "ORDER_PARTIAL", "ORDER_FILLED",
    "ORDER_REJECTED", "ORDER_CANCELLED", "ORDER_EXPIRED", "ORDER_UNKNOWN",
    "POSITION_OPENED", "POSITION_REVIEWED", "STOP_ADVANCED", "POSITION_CLOSED",
    "REENTRY_CONSIDERED", "REENTRY_ALLOWED", "REENTRY_REJECTED",
    "NEWS_BLOCK_ENTERED", "NEWS_BLOCK_CLEARED",
    "MODEL_USED", "RAG_USED",
    "RECONCILIATION_ACTION", "LEARNING_UPDATE",
})


class UnknownEventTypeError(ValueError):
    """Raised when append_event is asked to record a type not in EVENT_TYPES."""


@dataclass(frozen=True)
class JournalEvent:
    id: int
    chain_id: int
    chain_key: str
    sequence_in_chain: int
    event_type: str
    event_timestamp_utc: int
    recorded_at: str
    canonical_symbol: str
    broker_symbol: str | None
    strategy_key: str | None
    client_request_id: str | None
    broker_order_id: str | None
    broker_position_id: str | None
    broker_deal_id: str | None
    payload: dict


def get_or_create_chain(conn: sqlite3.Connection, chain_key: str, canonical_symbol: str) -> int:
    """Idempotent: the same `chain_key` always resolves to the same chain
    row. `canonical_symbol` is fixed at first creation — a chain concerns
    exactly one symbol for its whole lifetime, so a later call with a
    different symbol for the SAME `chain_key` is a caller bug, raised
    rather than silently accepted (which would corrupt the "traceable
    backward" guarantee directive section 56 requires)."""
    row = conn.execute(
        "SELECT id, canonical_symbol FROM decision_chains WHERE chain_key = ?", (chain_key,)
    ).fetchone()
    if row is not None:
        if row["canonical_symbol"] != canonical_symbol:
            raise ValueError(
                f"chain_key {chain_key!r} already exists for canonical_symbol "
                f"{row['canonical_symbol']!r}; cannot reuse it for {canonical_symbol!r}"
            )
        return row["id"]
    cursor = conn.execute(
        "INSERT INTO decision_chains (chain_key, canonical_symbol) VALUES (?, ?)",
        (chain_key, canonical_symbol),
    )
    return cursor.lastrowid


def _append_event_locked(
    conn: sqlite3.Connection,
    chain_key: str,
    event_type: str,
    event_timestamp_utc: int,
    canonical_symbol: str,
    payload: dict,
    *,
    broker_symbol: str | None = None,
    strategy_key: str | None = None,
    client_request_id: str | None = None,
    broker_order_id: str | None = None,
    broker_position_id: str | None = None,
    broker_deal_id: str | None = None,
) -> int:
    """Same insert `append_event()` performs, but assumes the caller
    ALREADY holds SQLite's write lock via an open `BEGIN IMMEDIATE`
    transaction — for callers that need this journal write to be part of
    a LARGER atomic unit (external review finding #16, e.g.
    `execution/reconciliation.py`'s broker-truth recovery, which must
    never leave local state half-applied between a position update, its
    deal rows, and the journal record of the same recovery). Never call
    this outside an already-open transaction: the sequence-number TOCTOU
    protection `append_event()` provides depends on the write lock being
    genuinely held, not on this function acquiring it."""
    if event_type not in EVENT_TYPES:
        raise UnknownEventTypeError(f"{event_type!r} is not a recognized journal event type")

    chain_id = get_or_create_chain(conn, chain_key, canonical_symbol)
    next_seq = conn.execute(
        "SELECT COALESCE(MAX(sequence_in_chain), 0) + 1 AS next_seq FROM journal_events WHERE chain_id = ?",
        (chain_id,),
    ).fetchone()["next_seq"]

    cursor = conn.execute(
        """
        INSERT INTO journal_events
            (chain_id, sequence_in_chain, event_type, event_timestamp_utc, canonical_symbol,
             broker_symbol, strategy_key, client_request_id, broker_order_id, broker_position_id,
             broker_deal_id, payload_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chain_id, next_seq, event_type, event_timestamp_utc, canonical_symbol,
            broker_symbol, strategy_key, client_request_id, broker_order_id, broker_position_id,
            broker_deal_id, json.dumps(payload, default=str),
        ),
    )
    return cursor.lastrowid


def append_event(
    conn: sqlite3.Connection,
    chain_key: str,
    event_type: str,
    event_timestamp_utc: int,
    canonical_symbol: str,
    payload: dict,
    *,
    broker_symbol: str | None = None,
    strategy_key: str | None = None,
    client_request_id: str | None = None,
    broker_order_id: str | None = None,
    broker_position_id: str | None = None,
    broker_deal_id: str | None = None,
) -> int:
    """Append one immutable event to the chain identified by `chain_key`
    (created on first use if it doesn't exist). Returns the new event's
    row id.

    Ordering within a chain is deterministic and race-safe:
    `sequence_in_chain` is computed as `1 + MAX(existing sequence for
    this chain)` and inserted in the SAME `BEGIN IMMEDIATE` transaction
    — never caller-supplied, and never a separate read-then-write that
    two concurrent callers could interleave. `BEGIN IMMEDIATE` (not a
    plain `BEGIN`) acquires SQLite's write lock up front, so a second
    concurrent `append_event()` call for the same chain blocks until the
    first commits, rather than both reading the same "next sequence"
    value and racing to insert it.

    A caller that needs this write to be part of a LARGER atomic unit
    (its own `BEGIN IMMEDIATE` already open) should call
    `_append_event_locked()` directly instead — this function always
    owns its own transaction boundary.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        event_id = _append_event_locked(
            conn, chain_key, event_type, event_timestamp_utc, canonical_symbol, payload,
            broker_symbol=broker_symbol, strategy_key=strategy_key, client_request_id=client_request_id,
            broker_order_id=broker_order_id, broker_position_id=broker_position_id, broker_deal_id=broker_deal_id,
        )
        conn.execute("COMMIT")
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise
    return event_id
