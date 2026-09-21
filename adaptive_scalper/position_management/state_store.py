"""Durable continuous position-management state (directive: "COMPLETE
POSITION PERSISTENCE").

`initial_monetary_risk` and `entry_regime` are written ONCE, at
`get_or_create_state()`, and no function in this module ever updates
them afterward — moving a protective stop must never redefine R
(directive section 21: "Initial monetary risk remains fixed... Moving SL
never redefines R"). `peak_r` is monotonic and persisted: `record_review()`
only ever sets it to `max(existing peak_r, new current_r)`, so a process
restart can never "reset" it and fool a later giveback check.
"""

from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass


class PositionStateConflictError(ValueError):
    """Raised when `get_or_create_state()` is called for a position that
    already has a persisted row, with a DIFFERENT `initial_monetary_risk`
    or `entry_regime` than what was originally recorded. These two fields
    are immutable by directive section 21 ("moving a stop never redefines
    R") — a caller supplying different values for an existing position is
    an integrity fault (a caller bug, a position_id collision, or state
    corruption), not something to silently paper over by returning the
    old row unchanged."""


@dataclass(frozen=True)
class PositionManagementState:
    id: int
    position_id: int
    initial_monetary_risk: float
    entry_regime: str
    latest_regime: str
    peak_r: float
    current_r: float | None
    last_review_at_utc: int | None
    threshold_cross_at_utc: int | None
    decision_at_utc: int | None
    request_at_utc: int | None
    broker_response_at_utc: int | None
    decision_r: float | None
    fill_r: float | None
    giveback_decision: float | None
    giveback_fill: float | None
    expected_slippage: float | None
    realized_slippage: float | None
    created_at_utc: int
    updated_at_utc: int


def _row_to_state(row: sqlite3.Row) -> PositionManagementState:
    return PositionManagementState(
        id=row["id"], position_id=row["position_id"], initial_monetary_risk=row["initial_monetary_risk"],
        entry_regime=row["entry_regime"], latest_regime=row["latest_regime"], peak_r=row["peak_r"],
        current_r=row["current_r"], last_review_at_utc=row["last_review_at_utc"],
        threshold_cross_at_utc=row["threshold_cross_at_utc"], decision_at_utc=row["decision_at_utc"],
        request_at_utc=row["request_at_utc"], broker_response_at_utc=row["broker_response_at_utc"],
        decision_r=row["decision_r"], fill_r=row["fill_r"], giveback_decision=row["giveback_decision"],
        giveback_fill=row["giveback_fill"], expected_slippage=row["expected_slippage"],
        realized_slippage=row["realized_slippage"], created_at_utc=row["created_at_utc"],
        updated_at_utc=row["updated_at_utc"],
    )


def get_state(conn: sqlite3.Connection, position_id: int) -> PositionManagementState | None:
    row = conn.execute(
        "SELECT * FROM position_management_state WHERE position_id = ?", (position_id,)
    ).fetchone()
    return _row_to_state(row) if row else None


def get_or_create_state(
    conn: sqlite3.Connection,
    position_id: int,
    *,
    initial_monetary_risk: float,
    entry_regime: str,
    now_utc: int | None = None,
) -> PositionManagementState:
    """Idempotent ONLY when the caller supplies the SAME immutable values
    as the existing row: `initial_monetary_risk`/`entry_regime` are never
    overwritten by a later call. A repeat call with DIFFERENT values
    raises `PositionStateConflictError` — silently keeping the old row
    while pretending success would hide a real integrity fault (directive
    section 21: moving a stop must never redefine R, and neither may a
    caller bug)."""
    if not math.isfinite(initial_monetary_risk) or initial_monetary_risk <= 0:
        raise ValueError(
            f"initial_monetary_risk must be positive and finite, got {initial_monetary_risk!r} "
            f"for position_id={position_id}"
        )

    existing = get_state(conn, position_id)
    if existing is not None:
        if (
            abs(existing.initial_monetary_risk - initial_monetary_risk) > 1e-9
            or existing.entry_regime != entry_regime
        ):
            raise PositionStateConflictError(
                f"position_id={position_id} already has persisted immutable state "
                f"(initial_monetary_risk={existing.initial_monetary_risk}, entry_regime={existing.entry_regime!r}) "
                f"which disagrees with the newly supplied values "
                f"(initial_monetary_risk={initial_monetary_risk}, entry_regime={entry_regime!r})"
            )
        return existing

    now = now_utc if now_utc is not None else int(time.time())
    conn.execute(
        """
        INSERT INTO position_management_state
            (position_id, initial_monetary_risk, entry_regime, latest_regime, peak_r, created_at_utc, updated_at_utc)
        VALUES (?, ?, ?, ?, 0.0, ?, ?)
        """,
        (position_id, initial_monetary_risk, entry_regime, entry_regime, now, now),
    )
    conn.commit()
    return get_state(conn, position_id)  # type: ignore[return-value]


def record_review(
    conn: sqlite3.Connection,
    position_id: int,
    *,
    current_r: float,
    latest_regime: str,
    now_utc: int | None = None,
) -> PositionManagementState:
    now = now_utc if now_utc is not None else int(time.time())
    existing = get_state(conn, position_id)
    if existing is None:
        raise ValueError(f"no position_management_state for position_id={position_id}; call get_or_create_state() first")
    new_peak_r = max(existing.peak_r, current_r)
    conn.execute(
        """
        UPDATE position_management_state
        SET current_r = ?, latest_regime = ?, peak_r = ?, last_review_at_utc = ?, updated_at_utc = ?
        WHERE position_id = ?
        """,
        (current_r, latest_regime, new_peak_r, now, now, position_id),
    )
    conn.commit()
    return get_state(conn, position_id)  # type: ignore[return-value]


def record_exit_decision(
    conn: sqlite3.Connection,
    position_id: int,
    *,
    decision_r: float,
    decision_at_utc: int,
    threshold_cross_at_utc: int | None = None,
    now_utc: int | None = None,
) -> PositionManagementState:
    now = now_utc if now_utc is not None else int(time.time())
    existing = get_state(conn, position_id)
    if existing is None:
        raise ValueError(f"no position_management_state for position_id={position_id}")
    giveback_decision = existing.peak_r - decision_r
    conn.execute(
        """
        UPDATE position_management_state
        SET decision_r = ?, decision_at_utc = ?,
            threshold_cross_at_utc = COALESCE(?, threshold_cross_at_utc),
            giveback_decision = ?, updated_at_utc = ?
        WHERE position_id = ?
        """,
        (decision_r, decision_at_utc, threshold_cross_at_utc, giveback_decision, now, position_id),
    )
    conn.commit()
    return get_state(conn, position_id)  # type: ignore[return-value]


def record_exit_request(
    conn: sqlite3.Connection,
    position_id: int,
    *,
    request_at_utc: int,
    expected_slippage: float | None = None,
    now_utc: int | None = None,
) -> PositionManagementState:
    now = now_utc if now_utc is not None else int(time.time())
    conn.execute(
        """
        UPDATE position_management_state
        SET request_at_utc = ?, expected_slippage = COALESCE(?, expected_slippage), updated_at_utc = ?
        WHERE position_id = ?
        """,
        (request_at_utc, expected_slippage, now, position_id),
    )
    conn.commit()
    return get_state(conn, position_id)  # type: ignore[return-value]


def record_risk_incident(
    conn: sqlite3.Connection,
    position_id: int,
    detail: str,
    *,
    now_utc: int | None = None,
) -> int:
    """Persist the degraded-health incident an unprovable/corrupted
    `initial_monetary_risk` represents (directive section 75 — a
    technical health problem, not just a financial-loss condition).
    Idempotent per position: a repeat call while an unresolved incident
    already exists for this position returns the EXISTING incident's id
    rather than spamming a new row every review cycle."""
    existing = conn.execute(
        "SELECT id FROM position_risk_incidents WHERE position_id = ? AND resolved_at_utc IS NULL",
        (position_id,),
    ).fetchone()
    if existing is not None:
        return existing["id"]

    now = now_utc if now_utc is not None else int(time.time())
    cursor = conn.execute(
        "INSERT INTO position_risk_incidents (position_id, incident_type, detail, detected_at_utc) "
        "VALUES (?, 'INVALID_INITIAL_RISK', ?, ?)",
        (position_id, detail, now),
    )
    conn.commit()
    return cursor.lastrowid


def has_unresolved_risk_incident(conn: sqlite3.Connection, position_id: int) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM position_risk_incidents WHERE position_id = ? AND resolved_at_utc IS NULL",
        (position_id,),
    ).fetchone()
    return row["n"] > 0


def record_exit_fill(
    conn: sqlite3.Connection,
    position_id: int,
    *,
    fill_r: float,
    broker_response_at_utc: int,
    realized_slippage: float | None = None,
    now_utc: int | None = None,
) -> PositionManagementState:
    now = now_utc if now_utc is not None else int(time.time())
    existing = get_state(conn, position_id)
    if existing is None:
        raise ValueError(f"no position_management_state for position_id={position_id}")
    giveback_fill = existing.peak_r - fill_r
    conn.execute(
        """
        UPDATE position_management_state
        SET fill_r = ?, broker_response_at_utc = ?, giveback_fill = ?,
            realized_slippage = COALESCE(?, realized_slippage), updated_at_utc = ?
        WHERE position_id = ?
        """,
        (fill_r, broker_response_at_utc, giveback_fill, realized_slippage, now, position_id),
    )
    conn.commit()
    return get_state(conn, position_id)  # type: ignore[return-value]
