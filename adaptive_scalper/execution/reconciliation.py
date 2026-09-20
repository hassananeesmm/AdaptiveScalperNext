"""Position reconciliation (directive section 31): broker is always
authoritative.

`BrokerPositionSnapshot` is a stand-in for the broker's CURRENTLY OPEN
position state — the real source will be `Gateway.positions_get()`,
which does not exist yet (directive's own build order places it
alongside `order_send`). `reconcile_positions()` itself is pure and
fully testable today against constructed/fake snapshots; once
`positions_get()` exists, its real output just needs converting to this
same shape — this function does not need to change.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

ORPHAN_BROKER_POSITION = "ORPHAN_BROKER_POSITION"
MISSING_LOCAL_POSITION = "MISSING_LOCAL_POSITION"
RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"


@dataclass(frozen=True)
class BrokerPositionSnapshot:
    broker_position_id: str
    canonical_symbol: str
    direction: str
    volume: float


@dataclass(frozen=True)
class LocalPositionRecord:
    id: int
    broker_position_id: str
    canonical_symbol: str
    direction: str
    volume: float
    entry_price: float
    initial_monetary_risk: float
    strategy_key: str | None


@dataclass(frozen=True)
class ReconciliationFinding:
    finding_type: str
    broker_position_id: str
    detail: str


def get_open_positions(conn: sqlite3.Connection) -> list[LocalPositionRecord]:
    rows = conn.execute("SELECT * FROM positions WHERE status = 'OPEN'").fetchall()
    return [
        LocalPositionRecord(
            id=r["id"], broker_position_id=r["broker_position_id"], canonical_symbol=r["canonical_symbol"],
            direction=r["direction"], volume=r["volume"], entry_price=r["entry_price"],
            initial_monetary_risk=r["initial_monetary_risk"], strategy_key=r["strategy_key"],
        )
        for r in rows
    ]


def reconcile_positions(
    local_open_positions: list[LocalPositionRecord],
    broker_positions: list[BrokerPositionSnapshot],
) -> list[ReconciliationFinding]:
    """Broker truth wins (directive section 31). Never substitutes
    current market price for a historical exit, never assumes a locally-
    tracked-open position is still open just because nothing has told
    this function otherwise — that is exactly what this comparison is
    for."""
    local_by_id = {p.broker_position_id: p for p in local_open_positions}
    broker_by_id = {p.broker_position_id: p for p in broker_positions}
    findings: list[ReconciliationFinding] = []

    for pid, bp in broker_by_id.items():
        if pid not in local_by_id:
            findings.append(ReconciliationFinding(
                ORPHAN_BROKER_POSITION, pid,
                f"broker reports an open position ({bp.canonical_symbol} {bp.direction} "
                f"vol={bp.volume}) with no matching local record — possible manual trade, "
                f"or a local write that failed after a genuinely successful broker fill",
            ))

    for pid, lp in local_by_id.items():
        if pid not in broker_by_id:
            findings.append(ReconciliationFinding(
                MISSING_LOCAL_POSITION, pid,
                f"local record shows position {pid} ({lp.canonical_symbol} {lp.direction} "
                f"vol={lp.volume}) as OPEN, but the broker no longer reports it — likely closed "
                f"by SL, TP, or manual action; local state must be updated from broker truth",
            ))

    for pid in set(local_by_id) & set(broker_by_id):
        lp, bp = local_by_id[pid], broker_by_id[pid]
        if abs(lp.volume - bp.volume) > 1e-9:
            findings.append(ReconciliationFinding(
                RECONCILIATION_MISMATCH, pid,
                f"volume mismatch: local={lp.volume} broker={bp.volume} (partial close/fill?)",
            ))
        if lp.direction != bp.direction:
            findings.append(ReconciliationFinding(
                RECONCILIATION_MISMATCH, pid,
                f"direction mismatch: local={lp.direction} broker={bp.direction} — "
                f"should never happen under normal operation, investigate immediately",
            ))

    return findings


def record_incident(
    conn: sqlite3.Connection,
    incident_type: str,
    detail: str,
    *,
    order_id: int | None = None,
    now_utc: int | None = None,
) -> int:
    import time as _time
    now = now_utc if now_utc is not None else int(_time.time())
    cursor = conn.execute(
        "INSERT INTO execution_incidents (order_id, incident_type, detail, detected_at_utc) VALUES (?, ?, ?, ?)",
        (order_id, incident_type, detail, now),
    )
    return cursor.lastrowid


def get_unresolved_incidents(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM execution_incidents WHERE resolved_at_utc IS NULL ORDER BY detected_at_utc"
    ).fetchall()


def resolve_incident(conn: sqlite3.Connection, incident_id: int, resolution: str, *, now_utc: int | None = None) -> None:
    import time as _time
    now = now_utc if now_utc is not None else int(_time.time())
    conn.execute(
        "UPDATE execution_incidents SET resolved_at_utc = ?, resolution = ? WHERE id = ?",
        (now, resolution, incident_id),
    )


def has_dangerous_unresolved_unknown(conn: sqlite3.Connection) -> bool:
    """Directive section 30: "A dangerous unresolved UNKNOWN may block
    new entries." Any unresolved `UNKNOWN_OUTCOME` incident counts —
    conservative by design, since ruling out duplicate exposure is
    exactly what remains unproven while it's unresolved."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM execution_incidents WHERE incident_type = 'UNKNOWN_OUTCOME' AND resolved_at_utc IS NULL"
    ).fetchone()
    return row["n"] > 0
