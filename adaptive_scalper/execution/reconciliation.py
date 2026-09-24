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
from dataclasses import dataclass, field

from adaptive_scalper.execution.state_machine import OrderState
from adaptive_scalper.execution.store import OrderRecord, get_active_orders, transition_order_state
from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.gateway.types import HistoricalDeal, PendingOrderSnapshot
from adaptive_scalper.journal.events import _append_event_locked, append_event

ORPHAN_BROKER_POSITION = "ORPHAN_BROKER_POSITION"
MISSING_LOCAL_POSITION = "MISSING_LOCAL_POSITION"
RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"
# External review finding #12 (2026-09-21): pending (RESTING) orders are
# real potential broker exposure too and must be reconciled just like
# open positions, not left to `positions_get()`-only reconciliation.
ORPHAN_BROKER_ORDER = "ORPHAN_BROKER_ORDER"
MISSING_LOCAL_ORDER = "MISSING_LOCAL_ORDER"
PENDING_MISMATCH = "PENDING_MISMATCH"

CLEAN = "CLEAN"
BLOCKING_MISMATCH = "BLOCKING_MISMATCH"
RECOVERED = "RECOVERED"

_BLOCKING_FINDING_TYPES = frozenset({ORPHAN_BROKER_POSITION, RECONCILIATION_MISMATCH})
_BLOCKING_ORDER_FINDING_TYPES = frozenset({ORPHAN_BROKER_ORDER, PENDING_MISMATCH})
# execution_incidents.incident_type's CHECK constraint (migration 0009)
# only allows the original four values -- pending-order findings map onto
# the existing generic RECONCILIATION_MISMATCH incident type when
# persisted, rather than requiring a schema-recreating ALTER; the richer
# Python-level finding_type is preserved in the incident's `detail` text
# and in the in-memory ReconciliationFinding/report the caller sees.
_INCIDENT_TYPE_FOR_ORDER_FINDING = {
    ORPHAN_BROKER_ORDER: "RECONCILIATION_MISMATCH",
    MISSING_LOCAL_ORDER: "RECONCILIATION_MISMATCH",
    PENDING_MISMATCH: "RECONCILIATION_MISMATCH",
}

# MT5 ENUM_ORDER_STATE (matches HistoricalOrder.state, undecoded there by
# design — see execution/unknown.py's identical map, kept in sync).
_MT5_ORDER_STATE_TO_ORDER_STATE: dict[int, OrderState] = {
    1: OrderState.RESTING, 2: OrderState.CANCELLED, 3: OrderState.PARTIAL,
    4: OrderState.FILLED, 5: OrderState.REJECTED, 6: OrderState.EXPIRED,
}

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


def reconcile_pending_orders(
    local_active_orders: list[OrderRecord],
    broker_pending: list[PendingOrderSnapshot],
) -> list[ReconciliationFinding]:
    """External review finding #12: broker truth wins for PENDING
    (RESTING) orders too, exactly like open positions. `local_active_orders`
    is every locally-tracked order still in a non-terminal state with a
    known `broker_order_id` (`execution.store.get_active_orders()`);
    `broker_pending` is the broker's CURRENT `orders_get()` universe."""
    # NOTE: ReconciliationFinding.broker_position_id is reused here to
    # carry the broker_order_id -- the dataclass is a generic "which
    # identifier does this finding concern" record shared with position
    # findings, not order-specific; callers of THIS function read it as
    # an order id.
    local_by_id = {str(o.broker_order_id): o for o in local_active_orders}
    broker_by_id = {str(p.broker_order_id): p for p in broker_pending}
    findings: list[ReconciliationFinding] = []

    for oid, bp in broker_by_id.items():
        if oid not in local_by_id:
            findings.append(ReconciliationFinding(
                ORPHAN_BROKER_ORDER, oid,
                f"broker reports a pending order ({bp.symbol} {bp.direction} vol={bp.volume}) with no "
                f"matching local active order — possible manual pending order, or a local write that "
                f"failed after a genuinely successful broker PLACED acknowledgement",
            ))

    for oid, lo in local_by_id.items():
        if oid not in broker_by_id:
            findings.append(ReconciliationFinding(
                MISSING_LOCAL_ORDER, oid,
                f"local record shows order {oid} ({lo.broker_symbol} {lo.direction} "
                f"vol={lo.requested_volume}) as active/RESTING, but the broker no longer reports it as "
                f"pending — may have filled, been cancelled, or expired; local state must be updated "
                f"from broker truth",
            ))

    for oid in set(local_by_id) & set(broker_by_id):
        lo, bp = local_by_id[oid], broker_by_id[oid]
        if lo.broker_symbol != bp.symbol:
            findings.append(ReconciliationFinding(
                PENDING_MISMATCH, oid, f"symbol mismatch: local={lo.broker_symbol} broker={bp.symbol}",
            ))
        if lo.direction != bp.direction:
            findings.append(ReconciliationFinding(
                PENDING_MISMATCH, oid, f"direction mismatch: local={lo.direction} broker={bp.direction}",
            ))

    return findings


def _recover_missing_local_order(
    conn: sqlite3.Connection, gateway: Gateway, local_order: OrderRecord, now: int, history_lookback_seconds: int,
) -> bool:
    """Attempts to resolve a RESTING-locally/missing-on-broker order from
    `history_orders_get()` — never assumes a state, only transitions on
    positive historical evidence. Returns `True` if the local order state
    was actually advanced. A FILLED/PARTIAL resolution additionally
    requires real entry-deal evidence (external review finding #8) before
    the local position is ever accounted — never fabricated from the
    history order alone."""
    history_orders = gateway.history_orders_get(local_order.created_at_utc, now + history_lookback_seconds)
    match = next((o for o in history_orders if str(o.ticket) == str(local_order.broker_order_id)), None)
    if match is None:
        return False
    mapped = _MT5_ORDER_STATE_TO_ORDER_STATE.get(match.state)
    if mapped is None or mapped == OrderState.RESTING:
        return False  # inconclusive/still-alive-looking evidence -- stays blocking, never guessed

    if mapped in (OrderState.CANCELLED, OrderState.EXPIRED, OrderState.REJECTED):
        transition_order_state(
            conn, local_order.id, mapped, detail="resolved via pending-order reconciliation history lookup",
            now_utc=now,
        )
        event_type = {
            OrderState.CANCELLED: "ORDER_CANCELLED", OrderState.EXPIRED: "ORDER_EXPIRED",
            OrderState.REJECTED: "ORDER_REJECTED",
        }[mapped]
        append_event(
            conn, f"pending-order-recovery:{local_order.id}", event_type, now, local_order.canonical_symbol,
            {"reason": "pending-order reconciliation", "history_order_state": match.state},
            broker_symbol=local_order.broker_symbol, broker_order_id=local_order.broker_order_id,
        )
        return True

    if mapped in (OrderState.FILLED, OrderState.PARTIAL):
        # Deferred imports to avoid a module-load-time cycle (entry_fills
        # imports from this module).
        from adaptive_scalper.execution.entry_fills import record_entry_fills
        from adaptive_scalper.execution.position_resolution import resolve_entry_fill_evidence

        evidence = resolve_entry_fill_evidence(
            gateway, broker_deal_id=None, broker_order_id=local_order.broker_order_id,
            window_from_utc=local_order.created_at_utc, window_to_utc=now + history_lookback_seconds,
        )
        if not evidence.resolved:
            return False  # history says FILLED/PARTIAL but no positive deal evidence -- never fabricate

        transition_order_state(
            conn, local_order.id, mapped, detail="resolved via pending-order reconciliation history lookup",
            broker_position_id=evidence.broker_position_id, now_utc=now,
        )
        record_entry_fills(
            conn, order_id=local_order.id, canonical_symbol=local_order.canonical_symbol,
            direction=local_order.direction, strategy_key=None, evidence=evidence,
            requested_volume=local_order.requested_volume,
            requested_monetary_risk=local_order.requested_monetary_risk or 0.0, now_utc=now,
        )
        append_event(
            conn, f"pending-order-recovery:{local_order.id}", "ORDER_FILLED" if mapped == OrderState.FILLED else "ORDER_PARTIAL",
            now, local_order.canonical_symbol,
            {"reason": "pending-order reconciliation", "volume": evidence.total_filled_volume},
            broker_symbol=local_order.broker_symbol, broker_order_id=local_order.broker_order_id,
            broker_position_id=evidence.broker_position_id,
        )
        return True

    return False


def record_incident(
    conn: sqlite3.Connection,
    incident_type: str,
    detail: str,
    *,
    order_id: int | None = None,
    dedup_key: str | None = None,
    now_utc: int | None = None,
) -> int:
    """External review finding #15: a periodic reconciliation cycle
    (directive: every ~0.5-1s) re-observing the SAME unresolved mismatch
    must not create an unbounded stream of duplicate rows. `dedup_key`
    identifies "this specific ongoing problem" — callers that reconcile
    the same identifiable broker_position_id/broker_order_id/order every
    run should pass a STABLE key for it (e.g.
    `f"{finding_type}:{broker_position_id}"`); a repeat call with the same
    key while the prior incident is still unresolved updates
    `last_seen_at_utc`/`occurrence_count` and returns the EXISTING row's
    id, never inserting a second one. Omitting `dedup_key` falls back to
    `f"{incident_type}:{order_id}"` when `order_id` is known, or a
    per-call unique row otherwise (e.g. a one-off `UNKNOWN_OUTCOME`
    finding tied to no stable identity) — never silently deduplicating
    two genuinely distinct incidents onto the same key."""
    import time as _time
    now = now_utc if now_utc is not None else int(_time.time())
    key = dedup_key if dedup_key is not None else (f"{incident_type}:order:{order_id}" if order_id is not None else None)

    if key is not None:
        existing = conn.execute(
            "SELECT id FROM execution_incidents WHERE dedup_key = ? AND resolved_at_utc IS NULL", (key,),
        ).fetchone()
        if existing is not None:
            conn.execute(
                "UPDATE execution_incidents SET last_seen_at_utc = ?, occurrence_count = occurrence_count + 1, "
                "detail = ? WHERE id = ?",
                (now, detail, existing["id"]),
            )
            return existing["id"]

    cursor = conn.execute(
        "INSERT INTO execution_incidents "
        "(order_id, incident_type, detail, detected_at_utc, dedup_key, first_seen_at_utc, last_seen_at_utc, occurrence_count) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
        (order_id, incident_type, detail, now, key, now, now),
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

# External review finding #10: never silently discard broker fee/entry-
# type/deal-type/order-ticket/magic/comment when persisting a deal row —
# these decode MT5's raw ENUM_DEAL_ENTRY / ENUM_DEAL_TYPE integer codes
# into the same text values `execution/entry_fills.py` uses for entry
# deals, so `deals.entry_type`/`deals.deal_type` are consistently decoded
# regardless of which module wrote the row.
_DEAL_ENTRY_NAMES = {0: "IN", 1: "OUT", 2: "INOUT", 3: "OUT_BY"}
_DEAL_TYPE_NAMES = {0: "BUY", 1: "SELL"}


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


def local_order_id_for_broker_order(conn: sqlite3.Connection, broker_order_id: str | None) -> int | None:
    """Never falsely links a broker deal to a local order row (external
    review finding #15): returns the local `orders.id` ONLY when a row
    genuinely exists for that exact broker order ticket; `None`
    (persisted as SQL NULL) when it cannot be proven — never the
    position's ENTRY order id standing in for a close it didn't create.
    Shared with `execution/entry_fills.py` (finding #9's entry-deal
    persistence), which needs the identical never-falsely-link guarantee
    for entry deals."""
    if not broker_order_id:
        return None
    row = conn.execute("SELECT id FROM orders WHERE broker_order_id = ?", (str(broker_order_id),)).fetchone()
    return row["id"] if row is not None else None


_local_order_id_for_broker_order = local_order_id_for_broker_order  # internal alias, this module's own call sites


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
                    (order_id, broker_deal_id, broker_position_id, price, volume, commission, swap, profit, fee,
                     entry_type, deal_type, broker_order_ticket, magic, comment, occurred_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id, str(deal.ticket), local.broker_position_id, deal.price,
                    deal.volume, deal.commission, deal.swap, deal.profit, deal.fee,
                    _DEAL_ENTRY_NAMES.get(deal.entry, str(deal.entry)),
                    _DEAL_TYPE_NAMES.get(deal.type, str(deal.type)),
                    str(deal.order) if deal.order else None, deal.magic, deal.comment, deal.time,
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
    # External review finding #12: the pending-order side of the same run.
    order_findings: list[ReconciliationFinding] = field(default_factory=list)
    recovered_order_ids: list[str] = field(default_factory=list)
    unrepaired_order_ids: list[str] = field(default_factory=list)


def run_reconciliation(
    conn: sqlite3.Connection,
    gateway: Gateway,
    chain_key: str,
    *,
    now_utc: int | None = None,
    history_lookback_seconds: int = 7 * 24 * 3600,
    journal_clean: bool = True,
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
        record_incident(
            conn, f.finding_type, f.detail, dedup_key=f"{f.finding_type}:position:{f.broker_position_id}", now_utc=now,
        )

    # External review finding #12: reconcile pending (RESTING) orders too
    # -- broker truth via orders_get() against every local active order.
    broker_pending = gateway.orders_get()
    local_active_orders = {str(o.broker_order_id): o for o in get_active_orders(conn)}
    order_findings = reconcile_pending_orders(list(local_active_orders.values()), broker_pending)

    recovered_order_ids: list[str] = []
    unrepaired_order_ids: list[str] = []
    blocking_order_findings: list[ReconciliationFinding] = []

    for f in order_findings:
        if f.finding_type != MISSING_LOCAL_ORDER:
            blocking_order_findings.append(f)
            continue
        local_order = local_active_orders[f.broker_position_id]  # keyed by broker_order_id here
        if _recover_missing_local_order(conn, gateway, local_order, now, history_lookback_seconds):
            recovered_order_ids.append(f.broker_position_id)
        else:
            unrepaired_order_ids.append(f.broker_position_id)
            blocking_order_findings.append(f)

    for f in blocking_order_findings:
        order_id = local_active_orders[f.broker_position_id].id if f.broker_position_id in local_active_orders else None
        record_incident(
            conn, _INCIDENT_TYPE_FOR_ORDER_FINDING[f.finding_type],
            f"[{f.finding_type}] {f.detail}", order_id=order_id,
            dedup_key=f"{f.finding_type}:order:{f.broker_position_id}", now_utc=now,
        )

    if blocking_findings or blocking_order_findings:
        status = BLOCKING_MISMATCH
    elif recovered_ids or recovered_order_ids:
        status = RECOVERED
    else:
        status = CLEAN

    if status == CLEAN and not journal_clean:
        # The runtime reconciles every ~1s; a CLEAN pass with nothing found
        # is not a decision worth an immutable journal row each time.
        return ReconciliationReport(
            status, findings, recovered_ids, unrepaired_ids,
            order_findings=order_findings, recovered_order_ids=recovered_order_ids,
            unrepaired_order_ids=unrepaired_order_ids,
        )
    append_event(
        conn, chain_key, "RECONCILIATION_ACTION", now, RECONCILIATION_CHAIN_SYMBOL,
        {
            "status": status,
            "finding_count": len(findings) + len(order_findings),
            "recovered_position_ids": recovered_ids,
            "unrepaired_position_ids": unrepaired_ids,
            "recovered_order_ids": recovered_order_ids,
            "unrepaired_order_ids": unrepaired_order_ids,
            "findings": [{"type": f.finding_type, "broker_position_id": f.broker_position_id} for f in findings],
            "order_findings": [{"type": f.finding_type, "broker_order_id": f.broker_position_id} for f in order_findings],
        },
    )
    return ReconciliationReport(
        status, findings, recovered_ids, unrepaired_ids,
        order_findings=order_findings, recovered_order_ids=recovered_order_ids,
        unrepaired_order_ids=unrepaired_order_ids,
    )
