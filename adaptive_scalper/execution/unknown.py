"""UNKNOWN order-outcome resolution (directive section 30).

"If an order or close outcome is uncertain: DO NOT BLINDLY RESEND.
Resolve using broker: open positions, current orders, order history,
deal history, IDs."

This module resolves against ORDER/DEAL HISTORY
(`history_orders_get`/`history_deals_get` — both already implemented and
live-tested in `gateway/`). `positions_get`/`orders_get` (the broker's
CURRENTLY OPEN, not-yet-historical state) do not exist in the gateway
yet — directive's own build order places them alongside `order_send`,
not before it (see `MASTER_BUILD_DIRECTIVE.md` section 118's execution
dependency chain). An order that is genuinely still resting/pending on
the broker right now, with no history entry yet, is therefore NOT
resolvable by this module alone today: `resolve_unknown_order()`
correctly reports `resolved=False` rather than guessing in that case —
never a fabricated "probably fine." The caller must keep the order in
`UNKNOWN`/`PENDING_RECONCILIATION` and keep NEW entries blocked until
`positions_get`/`orders_get` exist to complete the picture (tracked as a
named gap, not silently assumed complete).

MT5's raw `ENUM_ORDER_STATE` integer codes (matches
`gateway/types.py`'s `HistoricalOrder.state`, which deliberately stores
them undecoded):
0=STARTED 1=PLACED 2=CANCELED 3=PARTIAL 4=FILLED 5=REJECTED 6=EXPIRED
7=REQUEST_ADD 8=REQUEST_MODIFY 9=REQUEST_CANCEL
"""

from __future__ import annotations

from dataclasses import dataclass

from adaptive_scalper.execution.state_machine import OrderState
from adaptive_scalper.execution.store import OrderRecord
from adaptive_scalper.gateway.types import HistoricalOrder

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


def resolve_unknown_order(
    order: OrderRecord,
    history_orders: list[HistoricalOrder],
) -> UnknownResolution:
    """Attempt to resolve one UNKNOWN order against broker order history.

    Matching requires `order.broker_order_id` to already be known (i.e.
    the broker at least acknowledged the request before the outcome
    became uncertain) — an order that never got far enough to receive a
    broker order ID cannot be matched against history at all, and this
    correctly returns unresolved rather than guessing which history
    entry (if any) might correspond to it.
    """
    if order.broker_order_id is None:
        return UnknownResolution(
            False, None,
            "no broker_order_id recorded for this order — cannot match against broker history; "
            "the broker may never have received the request, or acknowledgement was itself lost. "
            "Remains UNKNOWN until positions_get/orders_get exist to check current broker state.",
        )

    match = next((o for o in history_orders if str(o.ticket) == str(order.broker_order_id)), None)
    if match is None:
        return UnknownResolution(
            False, None,
            f"broker_order_id={order.broker_order_id!r} not found in supplied order history — "
            f"may still be resting/pending (not yet in history) or the history window didn't cover it. "
            f"Remains UNKNOWN.",
        )

    new_state = _MT5_ORDER_STATE_TO_ORDER_STATE.get(match.state)
    if new_state is None:
        return UnknownResolution(
            False, None,
            f"broker_order_id={order.broker_order_id!r} found but its state={match.state} is not "
            f"one this module maps to a resolution (STARTED/REQUEST_* are broker-internal transients)",
            matched_broker_order_id=order.broker_order_id,
        )

    return UnknownResolution(
        True, new_state,
        f"resolved from broker order history: ticket={match.ticket} state={match.state} -> {new_state.value}",
        matched_broker_order_id=str(match.ticket),
        matched_broker_position_id=str(match.position_id) if match.position_id else None,
    )
