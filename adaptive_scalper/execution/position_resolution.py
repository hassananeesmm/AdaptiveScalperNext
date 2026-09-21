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
from adaptive_scalper.gateway.types import HistoricalDeal

# MT5 ENUM_DEAL_ENTRY: 0=IN (opened/added-to exposure).
_DEAL_ENTRY_IN = 0


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


@dataclass(frozen=True)
class EntryFillEvidence:
    """External review finding #8: an entry position must never be
    created with `entry_price=0.0` (or any other fallback) — its price
    must come from POSITIVELY PROVEN broker deal evidence, never
    `OrderSendResult.price_filled or 0.0`. `resolved=False` whenever that
    proof doesn't exist (no matching deal, or matching deals summing to a
    non-positive price/volume) — the caller must treat that exactly like
    an unresolved position (UNKNOWN/PENDING_RECONCILIATION), never
    inventing a price to proceed."""

    resolved: bool
    broker_position_id: str | None
    deals: tuple[HistoricalDeal, ...]
    total_filled_volume: float
    weighted_avg_price: float | None
    detail: str


def resolve_entry_fill_evidence(
    gateway: Gateway,
    *,
    broker_deal_id: str | None,
    broker_order_id: str | None,
    window_from_utc: int,
    window_to_utc: int,
) -> EntryFillEvidence:
    """Extends `resolve_opened_position_id()` with the actual matching
    entry (IN) deal(s) and a volume-weighted average fill price (external
    review finding #9: entry deals must be persisted, not just the
    position row — this is what gives the caller something real to
    persist). If more than one IN deal exists for the resolved position
    within the window (a genuinely multi-deal entry fill), ALL of them are
    returned and aggregated — never just the one matched by
    `broker_deal_id`."""
    position = resolve_opened_position_id(
        gateway, broker_deal_id=broker_deal_id, broker_order_id=broker_order_id,
        window_from_utc=window_from_utc, window_to_utc=window_to_utc,
    )
    if not position.resolved:
        return EntryFillEvidence(False, None, (), 0.0, None, position.detail)

    deals = gateway.history_deals_get(window_from_utc, window_to_utc)
    entry_deals = tuple(
        d for d in deals
        if str(d.position_id) == str(position.broker_position_id) and d.entry == _DEAL_ENTRY_IN and d.price > 0
        and d.volume > 0
    )
    if not entry_deals:
        return EntryFillEvidence(
            False, position.broker_position_id, (), 0.0, None,
            f"broker position {position.broker_position_id!r} resolved, but no positive-price/volume entry "
            f"(IN) deal evidence was found in broker history — refusing to fabricate an entry price",
        )

    total_volume = sum(d.volume for d in entry_deals)
    weighted_avg_price = sum(d.price * d.volume for d in entry_deals) / total_volume
    return EntryFillEvidence(
        True, position.broker_position_id, entry_deals, total_volume, weighted_avg_price,
        f"resolved {len(entry_deals)} entry deal(s) for position {position.broker_position_id}: "
        f"total_volume={total_volume}, weighted_avg_price={weighted_avg_price}",
    )
