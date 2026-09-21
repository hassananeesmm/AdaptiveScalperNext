"""Entry deal persistence + safe aggregate position recomputation
(external review findings #7, #8, #9, #10, 2026-09-21).

Replaces the prior `execution.store.create_local_position()`, whose
"idempotent no-op on a repeat `broker_position_id`" design was unsafe for
a SECOND fill trickling into the SAME position (it silently kept the
FIRST fill's stale volume/price/risk forever). `record_entry_fills()`:

1. Persists EVERY individual entry (IN) deal this call was given —
   `INSERT OR IGNORE` on `broker_deal_id`, so calling it again with
   overlapping evidence is a safe no-op for deals already recorded.
2. Recomputes the position's aggregate volume / volume-weighted-average
   entry price / initial monetary risk from ALL entry deals PERSISTED
   for this `broker_position_id` so far — not just the deals this one
   call was given — so a later call that discovers an ADDITIONAL fill
   correctly folds it into the running aggregate.
3. Refuses to mutate the position's aggregate fields once
   `position_management_state` already exists for it (R-based adaptive-
   exit management has started) — an entry fill discovered AFTER that
   point is a genuine broker/local desync, not something to silently
   reflow into an R calculation already in flight. It still persists the
   newly-discovered deal(s) (never dropped) and records an incident.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from adaptive_scalper.execution.position_resolution import EntryFillEvidence
from adaptive_scalper.execution.reconciliation import local_order_id_for_broker_order

# MT5 ENUM_DEAL_TYPE: 0=BUY, 1=SELL (only these two are meaningful for an
# entry (IN) deal on the three canonical symbols this project trades).
_DEAL_TYPE_NAMES = {0: "BUY", 1: "SELL"}

RECOMPUTED = "RECOMPUTED"
REFUSED_STATE_LOCKED = "REFUSED_STATE_LOCKED"
REFUSED_NO_EVIDENCE = "REFUSED_NO_EVIDENCE"


@dataclass(frozen=True)
class EntryFillRecordResult:
    status: str
    broker_position_id: str | None
    volume: float | None = None
    entry_price: float | None = None
    initial_monetary_risk: float | None = None
    detail: str = ""


def _insert_entry_deals(conn: sqlite3.Connection, *, broker_position_id: str, fallback_order_id: int | None, evidence: EntryFillEvidence) -> None:
    for deal in evidence.deals:
        order_id = local_order_id_for_broker_order(conn, str(deal.order) if deal.order else None) or fallback_order_id
        conn.execute(
            """
            INSERT OR IGNORE INTO deals
                (order_id, broker_deal_id, broker_position_id, price, volume, commission, swap, profit, fee,
                 entry_type, deal_type, broker_order_ticket, magic, comment, occurred_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'IN', ?, ?, ?, ?, ?)
            """,
            (
                order_id, str(deal.ticket), broker_position_id, deal.price, deal.volume, deal.commission,
                deal.swap, deal.profit, deal.fee, _DEAL_TYPE_NAMES.get(deal.type, str(deal.type)),
                str(deal.order) if deal.order else None, deal.magic, deal.comment, deal.time,
            ),
        )


def _has_active_r_management(conn: sqlite3.Connection, broker_position_id: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM position_management_state pms
        JOIN positions p ON p.id = pms.position_id
        WHERE p.broker_position_id = ?
        """,
        (str(broker_position_id),),
    ).fetchone()
    return row is not None


def record_entry_fills(
    conn: sqlite3.Connection,
    *,
    order_id: int,
    canonical_symbol: str,
    direction: str,
    strategy_key: str | None,
    evidence: EntryFillEvidence,
    requested_volume: float,
    requested_monetary_risk: float,
    now_utc: int | None = None,
) -> EntryFillRecordResult:
    """`evidence` must be `resolved` with a positive `weighted_avg_price`
    (external review finding #8) — callers that got an unresolved
    `EntryFillEvidence` must never call this; that is exactly the
    UNKNOWN/PENDING_RECONCILIATION case, not a zero-price local position."""
    if not evidence.resolved or evidence.broker_position_id is None or not evidence.weighted_avg_price or evidence.weighted_avg_price <= 0:
        return EntryFillRecordResult(
            REFUSED_NO_EVIDENCE, evidence.broker_position_id,
            detail=f"no positive-price entry-deal evidence to record: {evidence.detail}",
        )

    now = now_utc if now_utc is not None else int(time.time())
    broker_position_id = evidence.broker_position_id

    conn.execute("BEGIN IMMEDIATE")
    try:
        _insert_entry_deals(conn, broker_position_id=broker_position_id, fallback_order_id=order_id, evidence=evidence)

        if _has_active_r_management(conn, broker_position_id):
            # R-based adaptive-exit management is already tracking this
            # position — an additional fill discovered now must not
            # silently reflow into initial_monetary_risk/entry_price out
            # from under it. The deal is still persisted (above); record
            # an incident so a human/reconciliation pass investigates.
            conn.execute(
                "INSERT INTO execution_incidents (order_id, incident_type, detail, detected_at_utc) "
                "VALUES (?, 'RECONCILIATION_MISMATCH', ?, ?)",
                (
                    order_id,
                    f"additional entry deal(s) discovered for position {broker_position_id!r} after "
                    f"position_management_state already exists — NOT applied to the position's aggregate "
                    f"volume/entry_price/initial_monetary_risk; investigate before further R-based management",
                    now,
                ),
            )
            conn.execute("COMMIT")
            return EntryFillRecordResult(
                REFUSED_STATE_LOCKED, broker_position_id,
                detail="deal(s) persisted, but the position's aggregate was NOT mutated -- R-management already active; incident recorded",
            )

        rows = conn.execute(
            "SELECT price, volume FROM deals WHERE broker_position_id = ? AND entry_type = 'IN'",
            (broker_position_id,),
        ).fetchall()
        total_volume = sum(r["volume"] for r in rows)
        weighted_price = sum(r["price"] * r["volume"] for r in rows) / total_volume if total_volume > 0 else 0.0
        per_unit_risk = requested_monetary_risk / requested_volume if requested_volume > 0 else 0.0
        aggregate_risk = per_unit_risk * total_volume

        existing = conn.execute("SELECT id FROM positions WHERE broker_position_id = ?", (broker_position_id,)).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO positions
                    (broker_position_id, canonical_symbol, direction, volume, entry_price,
                     initial_monetary_risk, strategy_key, entry_order_id, status, opened_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
                """,
                (broker_position_id, canonical_symbol, direction, total_volume, weighted_price,
                 aggregate_risk, strategy_key, order_id, now),
            )
        else:
            conn.execute(
                "UPDATE positions SET volume = ?, entry_price = ?, initial_monetary_risk = ? WHERE id = ?",
                (total_volume, weighted_price, aggregate_risk, existing["id"]),
            )
        conn.execute("COMMIT")
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise

    return EntryFillRecordResult(
        RECOMPUTED, broker_position_id, volume=total_volume, entry_price=weighted_price,
        initial_monetary_risk=aggregate_risk,
        detail=f"aggregate recomputed from {len(rows)} persisted entry deal(s)",
    )
