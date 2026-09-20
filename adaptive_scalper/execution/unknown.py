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

Round 2 finding #7: `resolve_unknown_order()` above still requires
`order.broker_order_id` to already be known. `resolve_unknown_order_
without_broker_id()` is the SECONDARY correlation path for the case that
function cannot handle at all — an ambiguous send that lost broker
acknowledgement entirely, so `broker_order_id` was never recorded even
though the broker may genuinely have accepted the request. It correlates
on the compact request token `execution.service` embeds in every order's
`comment` (`execution.request_token`), narrowed by broker symbol, and
resolves ONLY when every matching candidate agrees — any ambiguity
(disagreeing states, or more than one distinct position id) stays
UNKNOWN with `conflict=True`, never guessed.
"""

from __future__ import annotations

from dataclasses import dataclass

from adaptive_scalper.execution.request_token import request_token
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


def resolve_unknown_order_without_broker_id(
    order: OrderRecord,
    *,
    current_positions: list[PositionSnapshot],
    current_pending_orders: list[PendingOrderSnapshot],
    history_orders: list[HistoricalOrder],
    history_deals: list[HistoricalDeal],
) -> UnknownResolution:
    """Secondary correlation path (execution-safety review round 2
    finding #7): resolves an order that lost broker acknowledgement
    ENTIRELY (`order.broker_order_id is None`) — a case
    `resolve_unknown_order()` above cannot handle at all, since it
    requires a known `broker_order_id` to match against.

    Correlates on the compact request TOKEN `execution.service` embeds
    in every `OrderRequest.comment` (`execution.request_token
    .embed_request_token()`) — NEVER on the full comment string, since
    brokers may append/modify/truncate it. Matches are further narrowed
    by `broker_symbol` (a token match on the wrong symbol is treated as
    coincidence, not evidence). Resolves ONLY when every matching
    candidate points to the SAME underlying broker state (same resolved
    order state, and the same position id where one is present) — any
    genuine ambiguity (matches disagreeing on state, or on more than one
    distinct position id) remains UNKNOWN (`conflict=True`), exactly like
    `resolve_unknown_order()`'s conflicting-evidence case. No match at
    all also remains UNKNOWN, new entries blocked either way.
    """
    if order.broker_order_id is not None:
        raise ValueError(
            "resolve_unknown_order_without_broker_id() is for orders with NO broker_order_id at all — "
            "use resolve_unknown_order() when one is known"
        )

    token = request_token(order.client_request_id)
    resolutions: list[tuple[OrderState, str | None]] = []

    for p in current_positions:
        if token in p.comment and p.symbol == order.broker_symbol:
            resolutions.append((OrderState.FILLED, p.broker_position_id))

    for o in current_pending_orders:
        if token in o.comment and o.symbol == order.broker_symbol:
            resolutions.append((OrderState.RESTING, None))

    for d in history_deals:
        if token in d.comment and d.symbol == order.broker_symbol:
            resolutions.append((OrderState.FILLED, str(d.position_id) if d.position_id else None))

    for h in history_orders:
        if token in h.comment and h.symbol == order.broker_symbol:
            mapped = _MT5_ORDER_STATE_TO_ORDER_STATE.get(h.state)
            if mapped is not None:
                resolutions.append((mapped, str(h.position_id) if h.position_id else None))

    if not resolutions:
        return UnknownResolution(
            False, None,
            f"no broker_order_id recorded, and no comment-token match for {token!r} on {order.broker_symbol!r} "
            f"found in any current/historical broker evidence — remains UNKNOWN, new entries blocked",
        )

    distinct_states = {state for state, _ in resolutions}
    distinct_position_ids = {pid for _, pid in resolutions if pid is not None}

    if len(distinct_states) > 1 or len(distinct_position_ids) > 1:
        return UnknownResolution(
            False, None,
            f"ambiguous comment-token match for {token!r}: {sorted(s.value for s in distinct_states)} / "
            f"positions {sorted(distinct_position_ids)} — cannot resolve safely, PENDING_RECONCILIATION "
            f"required, new entries blocked",
            conflict=True,
        )

    resolved_state = next(iter(distinct_states))
    resolved_position_id = next(iter(distinct_position_ids)) if distinct_position_ids else None
    return UnknownResolution(
        True, resolved_state,
        f"resolved to {resolved_state.value} via comment-token secondary correlation (token={token!r})",
        matched_broker_position_id=resolved_position_id,
    )
