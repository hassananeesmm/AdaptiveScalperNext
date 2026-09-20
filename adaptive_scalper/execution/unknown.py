"""UNKNOWN order-outcome resolution (directive section 30, execution-safety
review finding #3).

"If an order or close outcome is uncertain: DO NOT BLINDLY RESEND.
Resolve using broker: open positions, current orders, order history,
deal history, IDs."

`positions_get()`/`orders_get()` now exist alongside `history_orders_get()`/
`history_deals_get()`, so this module resolves against ALL FOUR sources:

- a currently-RESTING pending order matching this order's broker_order_id
  is strong current evidence the order is still alive, unfilled;
- a currently-OPEN broker position matching this order's
  broker_position_id is strong current evidence of a fill;
- a matching historical deal (matched by `deal.order == broker_order_id`)
  is evidence of a fill;
- a matching historical order, mapped through MT5's raw
  `ENUM_ORDER_STATE` codes, resolves REJECTED/CANCELLED/EXPIRED/PARTIAL/
  FILLED outcomes that are no longer current broker state.

Rules (directive section 30, execution-safety review): never blindly
resend. If ALL available evidence agrees on one outcome, resolve to it.
If evidence CONFLICTS (e.g. a historical order says REJECTED but a
broker position matching this order's identifiers still exists), that is
NOT resolved — it is `conflict=True`, `resolved=False`, requiring
PENDING_RECONCILIATION and blocking new entries. If there is no evidence
at all, it remains UNKNOWN (`resolved=False`, `conflict=False`) and new
entries stay blocked.

MT5's raw `ENUM_ORDER_STATE` integer codes (matches `gateway/types.py`'s
`HistoricalOrder.state`, which deliberately stores them undecoded):
0=STARTED 1=PLACED 2=CANCELED 3=PARTIAL 4=FILLED 5=REJECTED 6=EXPIRED
7=REQUEST_ADD 8=REQUEST_MODIFY 9=REQUEST_CANCEL
"""

from __future__ import annotations

from dataclasses import dataclass

from adaptive_scalper.execution.state_machine import OrderState
from adaptive_scalper.execution.store import OrderRecord
from adaptive_scalper.gateway.types import HistoricalDeal, HistoricalOrder, PendingOrderSnapshot, PositionSnapshot

_MT5_ORDER_STATE_TO_ORDER_STATE: dict[int, OrderState] = {
    1: OrderState.RESTING,   # PLACED — resting on the broker, unresolved-but-alive
    2: OrderState.CANCELLED,
    3: OrderState.PARTIAL,
    4: OrderState.FILLED,
    5: OrderState.REJECTED,
    6: OrderState.EXPIRED,
}


@dataclass(frozen=True)
class UnknownResolution:
    resolved: bool
    new_state: OrderState | None
    detail: str
    matched_broker_order_id: str | None = None
    matched_broker_position_id: str | None = None
    conflict: bool = False


def resolve_unknown_order(
    order: OrderRecord,
    *,
    current_positions: list[PositionSnapshot],
    current_pending_orders: list[PendingOrderSnapshot],
    history_orders: list[HistoricalOrder],
    history_deals: list[HistoricalDeal],
) -> UnknownResolution:
    """Attempt to resolve one UNKNOWN order against ALL available broker
    truth. Matching requires `order.broker_order_id` to already be known
    (i.e. the broker at least acknowledged the request before the
    outcome became uncertain) — an order that never got far enough to
    receive a broker order ID cannot be matched against anything, and
    this correctly returns unresolved rather than guessing.
    """
    if order.broker_order_id is None:
        return UnknownResolution(
            False, None,
            "no broker_order_id recorded for this order — cannot match against any broker evidence; "
            "the broker may never have received the request, or acknowledgement was itself lost. "
            "Remains UNKNOWN, new entries blocked.",
        )

    boid = str(order.broker_order_id)
    bpid = str(order.broker_position_id) if order.broker_position_id else None

    pending_match = next((o for o in current_pending_orders if str(o.broker_order_id) == boid), None)
    position_match = (
        next((p for p in current_positions if str(p.broker_position_id) == bpid), None)
        if bpid is not None else None
    )
    deal_match = next((d for d in history_deals if str(d.order) == boid), None)
    order_match = next((o for o in history_orders if str(o.ticket) == boid), None)

    evidence: list[tuple[str, OrderState]] = []
    if pending_match is not None:
        evidence.append(("current_pending_order", OrderState.RESTING))
    if position_match is not None:
        evidence.append(("current_broker_position", OrderState.FILLED))
    if deal_match is not None:
        evidence.append(("history_deal", OrderState.FILLED))
    if order_match is not None:
        mapped = _MT5_ORDER_STATE_TO_ORDER_STATE.get(order_match.state)
        if mapped is not None:
            evidence.append(("history_order", mapped))

    if not evidence:
        return UnknownResolution(
            False, None,
            f"no current or historical broker evidence found for broker_order_id={boid!r}; "
            f"remains UNKNOWN, new entries blocked",
        )

    distinct_states = {state for _, state in evidence}
    if len(distinct_states) > 1:
        sources = ", ".join(f"{src}={st.value}" for src, st in evidence)
        return UnknownResolution(
            False, None,
            f"conflicting broker evidence for broker_order_id={boid!r}: {sources} — "
            f"PENDING_RECONCILIATION required, new entries blocked",
            conflict=True,
        )

    resolved_state = next(iter(distinct_states))
    matched_position_id = None
    if position_match is not None:
        matched_position_id = position_match.broker_position_id
    elif deal_match is not None and deal_match.position_id:
        matched_position_id = str(deal_match.position_id)
    elif order_match is not None and order_match.position_id:
        matched_position_id = str(order_match.position_id)

    sources = [src for src, _ in evidence]
    return UnknownResolution(
        True, resolved_state,
        f"resolved to {resolved_state.value} from broker evidence: {sources}",
        matched_broker_order_id=boid,
        matched_broker_position_id=matched_position_id,
    )
