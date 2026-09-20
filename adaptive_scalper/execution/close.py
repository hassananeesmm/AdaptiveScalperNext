"""Safe position close (execution-safety review finding #2).

A close is NEVER simply "send an opposite DEAL" without identifying the
exact position — that risks creating opposite/unintended exposure,
especially on a hedging account where symbol+direction alone can't
disambiguate which position to reduce. This module:

- freshly re-fetches `positions_get()` immediately before send, so a
  position that already closed (SL/TP/manual action/race) is detected as
  ALREADY_CLOSED rather than triggering an opposite trade against a
  position that no longer exists;
- refuses to send if broker-reported direction/volume disagree with what
  the caller expected (VOLUME_MISMATCH) — partial close is disabled
  (directive: `partial_close_enabled = false`), so this only ever sends
  a request that closes the position's FULL current broker volume, and
  never a volume the caller didn't independently verify;
- always references the broker position ticket explicitly
  (`OrderRequest.position_ticket`), which `mt5_gateway._build_mt5_request`
  and `fake_gateway._simulate_close_deal` both require in order to treat
  the request as a close rather than a new open.
"""

from __future__ import annotations

from dataclasses import dataclass

from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.gateway.types import OrderAction, OrderRequest, OrderSendResult

ALREADY_CLOSED = "ALREADY_CLOSED"
VOLUME_MISMATCH = "VOLUME_MISMATCH"
SENT = "SENT"


@dataclass(frozen=True)
class CloseOutcome:
    status: str
    detail: str
    result: OrderSendResult | None = None


def _opposite_direction(direction: str) -> str:
    if direction == "BUY":
        return "SELL"
    if direction == "SELL":
        return "BUY"
    raise ValueError(f"direction must be 'BUY' or 'SELL', got {direction!r}")


def close_position_safely(
    gateway: Gateway,
    *,
    broker_position_id: str,
    expected_direction: str,
    expected_volume: float,
    magic: int = 0,
    comment: str = "",
    deviation_points: int = 20,
    filling_type: str | None = None,
) -> CloseOutcome:
    """Never sends when the position can't be proven to still exist with
    the expected direction/volume — the caller (position manager) should
    treat ALREADY_CLOSED/VOLUME_MISMATCH as a signal to reconcile, not
    retry blindly."""
    current_positions = {p.broker_position_id: p for p in gateway.positions_get()}
    live = current_positions.get(str(broker_position_id))
    if live is None:
        return CloseOutcome(
            ALREADY_CLOSED,
            f"position {broker_position_id!r} is no longer reported by the broker — treat as already "
            f"closed and reconcile; refusing to send an opposite trade against a position that doesn't exist",
        )

    if live.direction != expected_direction:
        return CloseOutcome(
            VOLUME_MISMATCH,
            f"position {broker_position_id!r} direction mismatch: broker={live.direction!r} "
            f"expected={expected_direction!r} — refusing to send",
        )

    if abs(live.volume - expected_volume) > 1e-9:
        return CloseOutcome(
            VOLUME_MISMATCH,
            f"position {broker_position_id!r} broker volume={live.volume} differs from expected="
            f"{expected_volume} — partial close is disabled, refusing to send a request that would not "
            f"close exactly the broker's current full volume",
        )

    close_direction = _opposite_direction(live.direction)
    request = OrderRequest(
        action=OrderAction.DEAL,
        symbol=live.symbol,
        direction=close_direction,
        volume=live.volume,
        position_ticket=int(broker_position_id),
        magic=magic,
        comment=comment,
        deviation_points=deviation_points,
        filling_type=filling_type,
    )
    result = gateway.order_send(request)
    return CloseOutcome(
        SENT,
        f"close request sent for position {broker_position_id!r}: {close_direction} vol={live.volume}",
        result=result,
    )
