"""Idempotent order persistence (directive section 29, execution-safety
review finding #6).

`create_order()` is idempotent on `client_request_id`: calling it twice
with the same id and the SAME immutable request fields returns the SAME
row (never a second order), which is what makes a retried submission —
e.g. after a network timeout where the caller doesn't know if the first
attempt reached the broker — safe.

Per finding #6: a retry that repeats `client_request_id` but disagrees on
any IMMUTABLE field (canonical symbol, broker symbol, direction, volume,
SL, TP, or chain_key) is NOT treated as a safe retry — silently returning
the old row unchanged in that case would hide a real caller bug (e.g. a
stale/corrupted proposal reusing an old id). It raises
`IdempotencyConflictError` instead, and no second order is ever created.

`transition_order_state()` is the ONLY way this module changes an
order's state, and it always goes through
`execution.state_machine.apply_transition()` first, so an illegal jump
(e.g. `PROPOSED` straight to `FILLED`) raises before it's ever persisted.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

from adaptive_scalper.execution.state_machine import OrderState, apply_transition


class IdempotencyConflictError(ValueError):
    """Raised by `create_order()` when `client_request_id` already exists
    but with DIFFERENT immutable request fields than this call supplied.
    Must never be caught and routed around into a resend — it means the
    caller's own understanding of what it already submitted is wrong."""


@dataclass(frozen=True)
class OrderRecord:
    id: int
    client_request_id: str
    chain_key: str | None
    canonical_symbol: str
    broker_symbol: str
    direction: str
    requested_volume: float
    stop_loss: float | None
    take_profit: float | None
    state: OrderState
    broker_order_id: str | None
    broker_position_id: str | None
    last_broker_retcode: int | None
    last_broker_comment: str | None
    created_at_utc: int
    updated_at_utc: int


def _row_to_order(row: sqlite3.Row) -> OrderRecord:
    return OrderRecord(
        id=row["id"], client_request_id=row["client_request_id"], chain_key=row["chain_key"],
        canonical_symbol=row["canonical_symbol"], broker_symbol=row["broker_symbol"],
        direction=row["direction"], requested_volume=row["requested_volume"],
        stop_loss=row["stop_loss"], take_profit=row["take_profit"], state=OrderState(row["state"]),
        broker_order_id=row["broker_order_id"], broker_position_id=row["broker_position_id"],
        last_broker_retcode=row["last_broker_retcode"], last_broker_comment=row["last_broker_comment"],
        created_at_utc=row["created_at_utc"], updated_at_utc=row["updated_at_utc"],
    )


def get_order_by_client_request_id(conn: sqlite3.Connection, client_request_id: str) -> OrderRecord | None:
    row = conn.execute("SELECT * FROM orders WHERE client_request_id = ?", (client_request_id,)).fetchone()
    return _row_to_order(row) if row else None


def create_order(
    conn: sqlite3.Connection,
    client_request_id: str,
    canonical_symbol: str,
    broker_symbol: str,
    direction: str,
    requested_volume: float,
    *,
    chain_key: str | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    now_utc: int | None = None,
) -> OrderRecord:
    """Idempotent for a genuine retry: if `client_request_id` already has
    a row with the SAME immutable fields, returns it UNCHANGED rather than
    creating a duplicate or mutating it — a caller that doesn't know
    whether its previous attempt succeeded can always safely call this
    again with the same id and the same request.

    Raises `IdempotencyConflictError` (finding #6) if `client_request_id`
    already has a row but any immutable field DIFFERS — that is never a
    safe retry, and no second order is created in this case either."""
    existing = get_order_by_client_request_id(conn, client_request_id)
    if existing is not None:
        immutable_fields = {
            "chain_key": (existing.chain_key, chain_key),
            "canonical_symbol": (existing.canonical_symbol, canonical_symbol),
            "broker_symbol": (existing.broker_symbol, broker_symbol),
            "direction": (existing.direction, direction),
            "requested_volume": (existing.requested_volume, requested_volume),
            "stop_loss": (existing.stop_loss, stop_loss),
            "take_profit": (existing.take_profit, take_profit),
        }
        mismatches = {k: v for k, v in immutable_fields.items() if v[0] != v[1]}
        if mismatches:
            raise IdempotencyConflictError(
                f"client_request_id={client_request_id!r} already exists (order id={existing.id}) with "
                f"DIFFERENT immutable fields: {mismatches} — this is not a safe retry; refusing to send, "
                f"no second order created"
            )
        return existing

    now = now_utc if now_utc is not None else int(time.time())
    conn.execute("BEGIN IMMEDIATE")
    try:
        cursor = conn.execute(
            """
            INSERT INTO orders
                (client_request_id, chain_key, canonical_symbol, broker_symbol, direction,
                 requested_volume, stop_loss, take_profit, state, created_at_utc, updated_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                client_request_id, chain_key, canonical_symbol, broker_symbol, direction,
                requested_volume, stop_loss, take_profit, OrderState.PROPOSED.value, now, now,
            ),
        )
        conn.execute(
            "INSERT INTO order_state_transitions (order_id, from_state, to_state, occurred_at_utc, detail) "
            "VALUES (?, NULL, ?, ?, ?)",
            (cursor.lastrowid, OrderState.PROPOSED.value, now, "order created"),
        )
        conn.execute("COMMIT")
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise
    return get_order_by_client_request_id(conn, client_request_id)  # type: ignore[return-value]


def transition_order_state(
    conn: sqlite3.Connection,
    order_id: int,
    to_state: OrderState,
    *,
    detail: str | None = None,
    broker_order_id: str | None = None,
    broker_position_id: str | None = None,
    broker_retcode: int | None = None,
    broker_comment: str | None = None,
    raw_broker_response: dict | None = None,
    now_utc: int | None = None,
) -> OrderRecord:
    """The ONLY way an order's state changes. Validates the transition
    against `execution.state_machine.ALLOWED_TRANSITIONS` (raises
    `InvalidTransitionError` if illegal) and records it in
    `order_state_transitions` before updating the order row, so the full
    history of an order's lifecycle is always reconstructable, not just
    its current state."""
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        raise ValueError(f"no order with id={order_id}")
    from_state = OrderState(row["state"])
    apply_transition(from_state, to_state)  # raises InvalidTransitionError if illegal

    now = now_utc if now_utc is not None else int(time.time())
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            INSERT INTO order_state_transitions (order_id, from_state, to_state, occurred_at_utc, detail)
            VALUES (?, ?, ?, ?, ?)
            """,
            (order_id, from_state.value, to_state.value, now, detail),
        )
        conn.execute(
            """
            UPDATE orders
            SET state = ?, updated_at_utc = ?,
                broker_order_id = COALESCE(?, broker_order_id),
                broker_position_id = COALESCE(?, broker_position_id),
                last_broker_retcode = COALESCE(?, last_broker_retcode),
                last_broker_comment = COALESCE(?, last_broker_comment),
                raw_broker_response_json = COALESCE(?, raw_broker_response_json)
            WHERE id = ?
            """,
            (
                to_state.value, now, broker_order_id, broker_position_id, broker_retcode,
                broker_comment, json.dumps(raw_broker_response) if raw_broker_response is not None else None,
                order_id,
            ),
        )
        conn.execute("COMMIT")
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise

    return get_order_by_client_request_id(conn, row["client_request_id"])  # type: ignore[return-value]


def get_order_state_history(conn: sqlite3.Connection, order_id: int) -> list[tuple[str | None, str, int]]:
    """(from_state, to_state, occurred_at_utc) tuples in order — the full
    reconstructable lifecycle of one order."""
    rows = conn.execute(
        "SELECT from_state, to_state, occurred_at_utc FROM order_state_transitions "
        "WHERE order_id = ? ORDER BY occurred_at_utc, id",
        (order_id,),
    ).fetchall()
    return [(r["from_state"], r["to_state"], r["occurred_at_utc"]) for r in rows]
