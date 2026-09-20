"""Authoritative resolution of the broker position opened by a fill
(execution-safety review finding #1).

MT5's raw `order_send()` result (`MqlTradeResult`) carries an order
ticket and a deal ticket, but NEVER a position ticket directly.
`gateway/mt5_gateway.py`/`gateway/fake_gateway.py` both now deliberately
report `broker_position_id=None` on every `OrderSendResult` — treating
the order ticket as the position ticket was a prior, unsafe assumption,
since MT5 does not guarantee they are the same value.

The true position ticket must be established from broker truth:
`deal.position_id` on the matching historical deal (`history_deals_get`,
matched by deal ticket), falling back to `order.position_id` on the
matching historical order (`history_orders_get`, matched by order
ticket) if the deal isn't found for some reason. Neither source found ->
unresolved; the caller must keep the order in UNKNOWN/PENDING_RECONCILIATION
rather than guess.
"""

from __future__ import annotations

from dataclasses import dataclass

from adaptive_scalper.gateway.protocol import Gateway


@dataclass(frozen=True)
class PositionResolution:
    resolved: bool
    broker_position_id: str | None
    detail: str


def resolve_opened_position_id(
    gateway: Gateway,
    *,
    broker_deal_id: str | None,
    broker_order_id: str | None,
    window_from_utc: int,
    window_to_utc: int,
) -> PositionResolution:
    """Looks up broker history for `window_from_utc..window_to_utc`
    (epoch seconds, UTC) — the caller should pass a window generously
    covering the moment the order was sent, since MT5's history
    endpoints are range-queried, not point-queried."""
    if broker_deal_id is not None:
        deals = gateway.history_deals_get(window_from_utc, window_to_utc)
        match = next((d for d in deals if str(d.ticket) == str(broker_deal_id)), None)
        if match is not None and match.position_id:
            return PositionResolution(
                True, str(match.position_id),
                f"resolved from deal history: deal ticket={match.ticket} position_id={match.position_id}",
            )

    if broker_order_id is not None:
        orders = gateway.history_orders_get(window_from_utc, window_to_utc)
        match = next((o for o in orders if str(o.ticket) == str(broker_order_id)), None)
        if match is not None and match.position_id:
            return PositionResolution(
                True, str(match.position_id),
                f"resolved from order history: order ticket={match.ticket} position_id={match.position_id}",
            )

    return PositionResolution(
        False, None,
        f"could not resolve a broker position id from deal history (deal_id={broker_deal_id!r}) or "
        f"order history (order_id={broker_order_id!r}) within the given window — order remains UNKNOWN",
    )
