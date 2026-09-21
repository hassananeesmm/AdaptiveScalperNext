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
- `RECOVERED` — every `MISSING_LOCAL_POSITION` finding was ACTUALLY
  repaired: `run_reconciliation()` queried `history_deals_get()` for the
  exact position id, found its authoritative closing deal, and
  atomically wrote that real close price/time/volume/commission/swap/
  profit into the local `positions`/`deals` tables, marked the position
  `CLOSED`, and journaled `POSITION_CLOSED` (execution-safety review
  round 2 finding #4 — a status named `RECOVERED` that hadn't actually
  repaired anything was misleading; it now only appears once repair has
  genuinely happened). Never substitutes CURRENT market price for a
  historical exit — only a real closing deal counts as evidence.
- `BLOCKING_MISMATCH` — an `ORPHAN_BROKER_POSITION`, a
  `RECONCILIATION_MISMATCH` (volume/direction disagreement), OR a
  `MISSING_LOCAL_POSITION` that could NOT be repaired (no closing deal
  found in broker history) exists; these represent possible unaccounted
  exposure and MUST block new entries until a human or a later
  reconciliation run resolves them.

Every run records `RECONCILIATION_ACTION` journal events and, for each
`BLOCKING_MISMATCH`-class finding (including an unrepairable
`MISSING_LOCAL_POSITION`), an `execution_incidents` row — broker truth is
authoritative and nothing here ever fabricates a historical close price
to paper over a gap.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.gateway.types import HistoricalDeal
from adaptive_scalper.journal.events import _append_event_locked, append_event

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
    opened_at_utc: int
    entry_order_id: int | None = None


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
            opened_at_utc=r["opened_at_utc"], entry_order_id=r["entry_order_id"],
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
    """Pure classification against the RAW findings — used by callers
    that never attempt recovery (e.g. simple monitoring). `run_reconciliation()`
    below reclassifies AFTER attempting repair, since a repaired
    `MISSING_LOCAL_POSITION` no longer belongs in either bucket."""
    if not findings:
        return CLEAN
    if any(f.finding_type in _BLOCKING_FINDING_TYPES for f in findings):
        return BLOCKING_MISMATCH
    return RECOVERED


# MT5 ENUM_DEAL_ENTRY: 0=IN (opened), 1=OUT (closed), 2=INOUT, 3=OUT_BY.
# External review finding #15: OUT is not the only deal entry that
# reduces/closes a position. INOUT (a netting-account deal that both
# reduces existing exposure AND opens new exposure in one atomic broker
# operation) and OUT_BY (a hedging-account deal that closes one position
# by an exactly opposite one) are both real, closing-relevant evidence —
# treating only OUT as "a close" would silently miss real exits.
_DEAL_ENTRY_OUT = 1
_DEAL_ENTRY_INOUT = 2
_DEAL_ENTRY_OUT_BY = 3
_CLOSING_DEAL_ENTRIES = frozenset({_DEAL_ENTRY_OUT, _DEAL_ENTRY_INOUT, _DEAL_ENTRY_OUT_BY})


def find_closing_deals(
    gateway: Gateway, broker_position_id: str, window_from_utc: int, window_to_utc: int,
) -> list[HistoricalDeal]:
    """EVERY closing-relevant deal (OUT/INOUT/OUT_BY) for
    `broker_position_id` in the given window, oldest first — never just
    the latest (external review finding #15: a position can close across
    several partial-close deals, and storing only the last one silently
    drops the others' commission/swap/profit from the local record).
    Never a guess, never the current market price standing in for a
    historical exit.

    Honest scope note on INOUT: this function returns INOUT deals as
    CLOSING evidence for the reduced portion of `broker_position_id`'s
    exposure. It does not attempt to split out and open a NEW local
    position for whatever additional exposure the same INOUT deal may
    have opened in the other direction — that remains a named gap (no
    canonical-symbol/strategy context exists at reconciliation time to
    attribute a freshly-opened position to), tracked in BUG_BACKLOG.md
    rather than silently mishandled."""
    deals = gateway.history_deals_get(window_from_utc, window_to_utc)
    matching = [
        d for d in deals if str(d.position_id) == str(broker_position_id) and d.entry in _CLOSING_DEAL_ENTRIES
    ]
    return sorted(matching, key=lambda d: d.time)


def find_closing_deal(
    gateway: Gateway, broker_position_id: str, window_from_utc: int, window_to_utc: int,
) -> HistoricalDeal | None:
    """The single LATEST closing-relevant deal, or `None` — a convenience
    wrapper over `find_closing_deals()` for callers that only need "the"
    final close moment (e.g. its `price`/`time`), not the full accounting.
    `run_reconciliation()` itself uses the plural function so every deal
    is actually recorded, not just this one."""
    deals = find_closing_deals(gateway, broker_position_id, window_from_utc, window_to_utc)
    return deals[-1] if deals else None


def _local_order_id_for_broker_order(conn: sqlite3.Connection, broker_order_id: str | None) -> int | None:
    """Never falsely links a broker deal to a local order row (external
    review finding #15): returns the local `orders.id` ONLY when a row
    genuinely exists for that exact broker order ticket; `None`
    (persisted as SQL NULL) when it cannot be proven — never the
    position's ENTRY order id standing in for a close it didn't create."""
    if not broker_order_id:
        return None
    row = conn.execute("SELECT id FROM orders WHERE broker_order_id = ?", (str(broker_order_id),)).fetchone()
    return row["id"] if row is not None else None


def _recover_missing_local_position(
    conn: sqlite3.Connection, local: LocalPositionRecord, closing_deals: list[HistoricalDeal], chain_key: str,
) -> None:
    """Atomically repairs local state from EVERY real broker closing deal
    for this position — marks the position CLOSED at the LATEST deal's
    time, records EACH deal (never just the last one), and journals ONE
    aggregated POSITION_CLOSED event — all in a SINGLE transaction
    (external review finding #16: broker truth being observed must not
    leave local SQLite recording half-applied between the position
    update, its deal rows, and the journal record of the same recovery).
    Never called with fabricated or current-market-price deals."""
    if not closing_deals:
        raise ValueError("_recover_missing_local_position() requires at least one closing deal")

    latest = closing_deals[-1]
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE positions SET status = 'CLOSED', closed_at_utc = ? WHERE id = ?",
            (latest.time, local.id),
        )
        for deal in closing_deals:
            order_id = _local_order_id_for_broker_order(conn, str(deal.order) if deal.order else None)
            conn.execute(
                """
                INSERT OR IGNORE INTO deals
                    (order_id, broker_deal_id, broker_position_id, price, volume, commission, swap, profit, occurred_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id, str(deal.ticket), local.broker_position_id, deal.price,
                    deal.volume, deal.commission, deal.swap, deal.profit, deal.time,
                ),
            )

        position_chain_key = f"{chain_key}:position:{local.broker_position_id}"
        _append_event_locked(
            conn, position_chain_key, "POSITION_CLOSED", latest.time, local.canonical_symbol,
            {
                "reason": "reconciliation recovery from broker history",
                "deal_count": len(closing_deals),
                "price": latest.price,
                "total_volume": sum(d.volume for d in closing_deals),
                "total_commission": sum(d.commission for d in closing_deals),
                "total_swap": sum(d.swap for d in closing_deals),
                "total_profit": sum(d.profit for d in closing_deals),
                "deals": [
                    {
                        "ticket": d.ticket, "entry": d.entry, "price": d.price, "volume": d.volume,
                        "commission": d.commission, "swap": d.swap, "profit": d.profit, "time": d.time,
                    }
                    for d in closing_deals
                ],
            },
            strategy_key=local.strategy_key, broker_position_id=local.broker_position_id,
            broker_deal_id=str(latest.ticket),
        )
        conn.execute("COMMIT")
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise


@dataclass(frozen=True)
class ReconciliationReport:
    status: str
    findings: list[ReconciliationFinding]
    recovered_position_ids: list[str]
    unrepaired_position_ids: list[str]


def run_reconciliation(
    conn: sqlite3.Connection,
    gateway: Gateway,
    chain_key: str,
    *,
    now_utc: int | None = None,
    history_lookback_seconds: int = 7 * 24 * 3600,
) -> ReconciliationReport:
    """The real reconciliation entry point (execution-safety review
    finding #4, upgraded in round 2 to perform ACTUAL repair). Meant to
    be run at startup, on reconnect, before enabling DEMO entries, after
    every order submission/uncertain response/fill/modification/close,
    and periodically — the caller decides the cadence; this function is
    idempotent (a repeat run against an already-repaired position finds
    it no longer OPEN locally, so there is nothing left to repair).

    `gateway` should always be a `SynchronizedGateway` in real operation
    (directive/finding #4's shared-serialization requirement) — this
    function itself has no opinion on that; it only calls
    `Gateway.positions_get()`/`history_deals_get()` through whatever was
    passed in.
    """
    now = now_utc if now_utc is not None else int(time.time())

    broker_raw = gateway.positions_get()
    broker_positions = [
        BrokerPositionSnapshot(p.broker_position_id, p.symbol, p.direction, p.volume) for p in broker_raw
    ]
    local_positions = {p.broker_position_id: p for p in get_open_positions(conn)}
    findings = reconcile_positions(list(local_positions.values()), broker_positions)

    recovered_ids: list[str] = []
    unrepaired_ids: list[str] = []
    blocking_findings: list[ReconciliationFinding] = []

    for f in findings:
        if f.finding_type != MISSING_LOCAL_POSITION:
            blocking_findings.append(f)
            continue
        local = local_positions[f.broker_position_id]
        # ALL closing-relevant deals (OUT/INOUT/OUT_BY), not just the
        # latest (finding #15) — a position can close across several
        # partial-close deals, and every one of them is real cost/profit.
        closing_deals = find_closing_deals(
            gateway, f.broker_position_id, local.opened_at_utc, now + history_lookback_seconds,
        )
        if not closing_deals:
            unrepaired_ids.append(f.broker_position_id)
            blocking_findings.append(f)
            continue
        # Position update + every deal row + the POSITION_CLOSED journal
        # event for this recovery are ONE atomic transaction (finding
        # #16) — _recover_missing_local_position() itself journals under
        # a per-position sub-chain (a chain_key maps to exactly one
        # canonical_symbol for its lifetime — journal.events
        # .get_or_create_chain enforces this — and this run's own
        # chain_key is tied to the account-wide RECONCILIATION
        # pseudo-symbol, not any one position's real symbol).
        _recover_missing_local_position(conn, local, closing_deals, chain_key)
        recovered_ids.append(f.broker_position_id)

    for f in blocking_findings:
        record_incident(conn, f.finding_type, f.detail, now_utc=now)

    if blocking_findings:
        status = BLOCKING_MISMATCH
    elif recovered_ids:
        status = RECOVERED
    else:
        status = CLEAN

    append_event(
        conn, chain_key, "RECONCILIATION_ACTION", now, RECONCILIATION_CHAIN_SYMBOL,
        {
            "status": status,
            "finding_count": len(findings),
            "recovered_position_ids": recovered_ids,
            "unrepaired_position_ids": unrepaired_ids,
            "findings": [{"type": f.finding_type, "broker_position_id": f.broker_position_id} for f in findings],
        },
    )
    return ReconciliationReport(status, findings, recovered_ids, unrepaired_ids)
