"""Recovery of interrupted and UNKNOWN orders (directive sections 29-31).

Two gaps the broker chaos tests exposed, closed here:

1. A process that dies after an order was marked SUBMITTED (or
   ACCEPTED, before its position was resolved) leaves a row that no
   incident points at, so nothing blocked new exposure while the broker
   may already hold a position. `quarantine_interrupted_submissions()`
   runs ONCE at startup, before this process submits anything: every
   such order becomes UNKNOWN with an `UNKNOWN_OUTCOME` incident, which
   `core.final_permission` treats as a dangerous UNKNOWN.

2. `execution.unknown`'s resolvers are pure; nothing APPLIED their
   verdict, so one UNKNOWN blocked new entries forever.
   `apply_unknown_resolutions()` gathers broker truth ONCE per pass,
   resolves each UNKNOWN/PENDING_RECONCILIATION order, and only on
   POSITIVE broker proof transitions it and resolves its incident:

   - FILLED/PARTIAL with a position id AND positive-price entry (IN) deal
     evidence -> order FILLED/PARTIAL, entry deals + local position
     recorded exactly as the live path records them;
   - REJECTED/CANCELLED/EXPIRED -> terminal, nothing to account;
   - RESTING, conflicting evidence, no evidence, or a fill without deal
     evidence -> stays unresolved (UNKNOWN -> PENDING_RECONCILIATION on
     conflict/resting); the incident stays open and new exposure stays
     blocked. Never guessed.

Neither function sends, modifies or cancels anything at the broker.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from adaptive_scalper.execution.entry_fills import record_entry_fills
from adaptive_scalper.execution.position_resolution import EntryFillEvidence
from adaptive_scalper.execution.reconciliation import record_incident, resolve_incident
from adaptive_scalper.execution.state_machine import OrderState
from adaptive_scalper.execution.store import (
    OrderRecord,
    _row_to_order,
    record_order_risk_accounting,
    transition_order_state,
)
from adaptive_scalper.execution.unknown import (
    DEFAULT_UNKNOWN_CORRELATION_TIME_WINDOW_SECONDS,
    resolve_unknown_order,
    resolve_unknown_order_without_broker_id,
)
from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.journal.events import append_event

INTERRUPTED_STATES = (OrderState.SUBMITTED, OrderState.ACCEPTED)
UNRESOLVED_STATES = (OrderState.UNKNOWN, OrderState.PENDING_RECONCILIATION)
_DEAL_ENTRY_IN = 0


@dataclass(frozen=True)
class RecoveryOutcome:
    order_id: int
    resolved: bool
    new_state: str | None
    detail: str
    broker_position_id: str | None = None


def _orders_in(conn: sqlite3.Connection, states) -> list[OrderRecord]:
    marks = ", ".join("?" * len(states))
    rows = conn.execute(
        f"SELECT * FROM orders WHERE state IN ({marks}) ORDER BY id", tuple(s.value for s in states),
    ).fetchall()
    return [_row_to_order(r) for r in rows]


def _journal(conn, order: OrderRecord, event_type: str, now: int, payload: dict, **ids) -> None:
    if order.chain_key:
        append_event(conn, order.chain_key, event_type, now, order.canonical_symbol, payload, strategy_key=None, **ids)


def quarantine_interrupted_submissions(conn: sqlite3.Connection, *, now_utc: int | None = None) -> list[int]:
    """STARTUP ONLY, before this process submits any order: an order still
    SUBMITTED/ACCEPTED belongs to a previous process that died mid-flight."""
    now = now_utc if now_utc is not None else int(time.time())
    quarantined = []
    for order in _orders_in(conn, INTERRUPTED_STATES):
        detail = (
            f"process restarted with order in {order.state.value}: the previous process died before the "
            f"broker outcome was persisted -- UNKNOWN until broker truth resolves it, never resent"
        )
        transition_order_state(conn, order.id, OrderState.UNKNOWN, detail=detail, now_utc=now)
        record_incident(conn, "UNKNOWN_OUTCOME", detail, order_id=order.id, now_utc=now)
        _journal(conn, order, "ORDER_UNKNOWN", now, {"detail": detail, "context": "startup quarantine"})
        quarantined.append(order.id)
    conn.commit()
    return quarantined


def _strategy_key_for_chain(conn: sqlite3.Connection, chain_key: str | None) -> str | None:
    if not chain_key:
        return None
    row = conn.execute(
        "SELECT je.strategy_key FROM journal_events je JOIN decision_chains dc ON dc.id = je.chain_id "
        "WHERE dc.chain_key = ? AND je.strategy_key IS NOT NULL ORDER BY je.sequence_in_chain LIMIT 1",
        (chain_key,),
    ).fetchone()
    return row["strategy_key"] if row is not None else None


def _resolve_order_incidents(conn: sqlite3.Connection, order_id: int, resolution: str, now: int) -> None:
    for row in conn.execute(
        "SELECT id FROM execution_incidents WHERE order_id = ? AND incident_type = 'UNKNOWN_OUTCOME' "
        "AND resolved_at_utc IS NULL", (order_id,),
    ).fetchall():
        resolve_incident(conn, row["id"], resolution, now_utc=now)


def apply_unknown_resolutions(
    conn: sqlite3.Connection, gateway: Gateway, *, now_utc: int | None = None,
    time_window_seconds: int = DEFAULT_UNKNOWN_CORRELATION_TIME_WINDOW_SECONDS,
) -> list[RecoveryOutcome]:
    now = now_utc if now_utc is not None else int(time.time())
    orders = _orders_in(conn, UNRESOLVED_STATES)
    if not orders:
        return []

    positions = gateway.positions_get()
    pending = gateway.orders_get()
    window_from = min(o.created_at_utc for o in orders) - time_window_seconds
    window_to = now + time_window_seconds
    history_orders = gateway.history_orders_get(window_from, window_to)
    history_deals = gateway.history_deals_get(window_from, window_to)

    outcomes = []
    for order in orders:
        kwargs = dict(current_positions=positions, current_pending_orders=pending,
                      history_orders=history_orders, history_deals=history_deals)
        if order.broker_order_id is not None:
            res = resolve_unknown_order(order, **kwargs)
        else:
            res = resolve_unknown_order_without_broker_id(order, time_window_seconds=time_window_seconds, **kwargs)

        if not res.resolved or res.new_state == OrderState.RESTING:
            detail = res.detail if res.new_state != OrderState.RESTING else (
                "order is RESTING at the broker -- needs pending-order reconciliation, not an assumed outcome"
            )
            if (res.conflict or res.new_state == OrderState.RESTING) and order.state == OrderState.UNKNOWN:
                transition_order_state(conn, order.id, OrderState.PENDING_RECONCILIATION, detail=detail, now_utc=now)
            outcomes.append(RecoveryOutcome(order.id, False, None, detail))
            continue

        if res.new_state in (OrderState.FILLED, OrderState.PARTIAL):
            outcomes.append(_apply_fill(conn, order, res.new_state, res.matched_broker_position_id,
                                        history_deals, now))
            continue

        # REJECTED / CANCELLED / EXPIRED: terminal, no exposure to account.
        transition_order_state(conn, order.id, res.new_state, detail=res.detail, now_utc=now)
        _resolve_order_incidents(conn, order.id, f"broker truth: {res.new_state.value}", now)
        _journal(conn, order, "RECONCILIATION_ACTION", now, {"unknown_resolved_to": res.new_state.value,
                                                             "detail": res.detail})
        outcomes.append(RecoveryOutcome(order.id, True, res.new_state.value, res.detail))
    conn.commit()
    return outcomes


def _apply_fill(
    conn: sqlite3.Connection, order: OrderRecord, state: OrderState, broker_position_id: str | None,
    history_deals, now: int,
) -> RecoveryOutcome:
    if broker_position_id is None:
        return RecoveryOutcome(order.id, False, None, "broker evidence says filled, but no position id -- unresolved")
    entry_deals = tuple(
        d for d in history_deals
        if str(d.position_id) == str(broker_position_id) and d.entry == _DEAL_ENTRY_IN and d.price > 0 and d.volume > 0
    )
    if not entry_deals:
        return RecoveryOutcome(
            order.id, False, None,
            f"broker says filled into position {broker_position_id}, but no positive-price entry deal evidence "
            f"exists yet -- refusing to fabricate an entry price; unresolved",
        )
    total_volume = sum(d.volume for d in entry_deals)
    evidence = EntryFillEvidence(
        True, str(broker_position_id), entry_deals, total_volume,
        sum(d.price * d.volume for d in entry_deals) / total_volume,
        f"recovered {len(entry_deals)} entry deal(s) from broker history",
    )
    requested_risk = order.requested_monetary_risk or 0.0
    transition_order_state(
        conn, order.id, state, detail=f"UNKNOWN resolved from broker history: {evidence.detail}",
        broker_position_id=str(broker_position_id), now_utc=now,
    )
    fill = record_entry_fills(
        conn, order_id=order.id, canonical_symbol=order.canonical_symbol, direction=order.direction,
        strategy_key=_strategy_key_for_chain(conn, order.chain_key), evidence=evidence,
        requested_volume=order.requested_volume, requested_monetary_risk=requested_risk, now_utc=now,
    )
    record_order_risk_accounting(
        conn, order.id, filled_volume=total_volume, filled_initial_monetary_risk=(fill.initial_monetary_risk or 0.0),
        remaining_volume=max(0.0, order.requested_volume - total_volume),
        remaining_pending_monetary_risk=max(0.0, requested_risk - (fill.initial_monetary_risk or 0.0)), now_utc=now,
    )
    _resolve_order_incidents(conn, order.id, f"broker truth: {state.value} into position {broker_position_id}", now)
    _journal(conn, order, "POSITION_OPENED", now, {
        "context": "UNKNOWN resolved from broker history", "volume": fill.volume, "price": fill.entry_price,
        "initial_monetary_risk": fill.initial_monetary_risk,
    }, broker_position_id=str(broker_position_id))
    return RecoveryOutcome(order.id, True, state.value, evidence.detail, broker_position_id=str(broker_position_id))
