"""Position reconciliation (directive section 31, execution-safety review
finding #4): broker is always authoritative.

`reconcile_positions()` is pure and fully testable against constructed/
fake snapshots. `run_reconciliation()` is the real orchestration entry
point: it fetches CURRENT broker truth via `Gateway.positions_get()`
(through the shared `SynchronizedGateway`, per directive/finding #4 —
every real caller passes a `SynchronizedGateway`, never a raw
`Mt5Gateway`, so concurrent callers — the position manager, the entry
scanner, the dashboard, a reconciliation cron — never race against the
same MT5 terminal connection), compares it against locally-tracked open
positions, and reduces the findings to ONE deterministic actionable
status:

- `CLEAN` — no findings; broker and local state agree.
- `BLOCKING_MISMATCH` — an `ORPHAN_BROKER_POSITION` or a
  `RECONCILIATION_MISMATCH` (volume/direction disagreement) exists; these
  represent possible unaccounted exposure and MUST block new entries
  until a human or the recovery path resolves them.
- `RECOVERED` — only `MISSING_LOCAL_POSITION` findings exist (broker no
  longer reports a position the local DB still marks OPEN — e.g. closed
  by SL/TP/manual action). This is locally recoverable by updating the
  local record from broker truth; it does not represent unaccounted
  exposure, so it does not block new entries on its own.

Every run records `RECONCILIATION_ACTION` journal events and, for each
`BLOCKING_MISMATCH`-class finding, an `execution_incidents` row — broker
truth is authoritative and nothing here ever fabricates a historical
close price to paper over a gap.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.journal.events import append_event

ORPHAN_BROKER_POSITION = "ORPHAN_BROKER_POSITION"
MISSING_LOCAL_POSITION = "MISSING_LOCAL_POSITION"
RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"

CLEAN = "CLEAN"
BLOCKING_MISMATCH = "BLOCKING_MISMATCH"
RECOVERED = "RECOVERED"

_BLOCKING_FINDING_TYPES = frozenset({ORPHAN_BROKER_POSITION, RECONCILIATION_MISMATCH})

# journal_events.canonical_symbol is NOT NULL, and a reconciliation run is
# account-wide (spans all symbols), not tied to one of the three canonical
# trading symbols — this pseudo-symbol is used ONLY for the journal chain
# a reconciliation run's RECONCILIATION_ACTION events are recorded under,
# never accepted anywhere a real tradable symbol is expected.
RECONCILIATION_CHAIN_SYMBOL = "RECONCILIATION"


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


def classify_reconciliation(findings: list[ReconciliationFinding]) -> str:
    if not findings:
        return CLEAN
    if any(f.finding_type in _BLOCKING_FINDING_TYPES for f in findings):
        return BLOCKING_MISMATCH
    return RECOVERED  # only MISSING_LOCAL_POSITION findings — locally recoverable, not blocking


@dataclass(frozen=True)
class ReconciliationReport:
    status: str
    findings: list[ReconciliationFinding]


def run_reconciliation(
    conn: sqlite3.Connection,
    gateway: Gateway,
    chain_key: str,
    *,
    now_utc: int | None = None,
) -> ReconciliationReport:
    """The real reconciliation entry point (execution-safety review
    finding #4). Meant to be run at startup, on reconnect, before
    enabling DEMO entries, after every order submission/uncertain
    response/fill/modification/close, and periodically — the caller
    decides the cadence; this function is idempotent and side-effect-free
    beyond recording the findings it actually detects.

    `gateway` should always be a `SynchronizedGateway` in real operation
    (directive/finding #4's shared-serialization requirement) — this
    function itself has no opinion on that; it only calls
    `Gateway.positions_get()` through whatever was passed in.
    """
    broker_raw = gateway.positions_get()
    broker_positions = [
        BrokerPositionSnapshot(p.broker_position_id, p.symbol, p.direction, p.volume) for p in broker_raw
    ]
    local_positions = get_open_positions(conn)
    findings = reconcile_positions(local_positions, broker_positions)
    status = classify_reconciliation(findings)

    now = now_utc if now_utc is not None else int(time.time())
    for f in findings:
        if f.finding_type in _BLOCKING_FINDING_TYPES:
            record_incident(conn, f.finding_type, f.detail, now_utc=now)

    append_event(
        conn, chain_key, "RECONCILIATION_ACTION", now, RECONCILIATION_CHAIN_SYMBOL,
        {
            "status": status,
            "finding_count": len(findings),
            "findings": [{"type": f.finding_type, "broker_position_id": f.broker_position_id} for f in findings],
        },
    )
    return ReconciliationReport(status, findings)
