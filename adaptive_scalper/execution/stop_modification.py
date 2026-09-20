"""Safe protective-stop modification (directive: "Protective Stop
Execution", directive section 23).

Broker SL/TP remains the hard, always-on protection; this module's only
job is ADVANCING it — never loosening it, and never touching it when the
outcome is uncertain. It follows the same two-round-of-fresh-checks
pattern `execution/close.py` established: fresh DEMO verification and
fresh broker position state, once before `order_check`, and again
immediately before `order_send`.

Monotonic guarantee (directive section 23 — reused, not reimplemented:
`position_management.adaptive_exit.resolve_new_stop_price()` is the
single source of truth for "never move a protective stop backward," used
here exactly as `adaptive_exit` uses it when deciding a new stop value).
If the broker's CURRENT stop has already reached or passed the proposed
one (by this module's own request, a broker-side trail, or any other
process), this module sends NOTHING — `NO_CHANGE`, never a request that
could only ever worsen or no-op the position's protection.

Respects `SymbolSpec.trade_stops_level` (minimum broker-required distance
from the current price, in points) — refusing a too-close stop locally
rather than discovering it only via a failed `order_check`/`order_send`.
`trade_freeze_level` is a named gap: MT5 doesn't expose "is this specific
order currently frozen" as a symbol-level flag by itself, and this module
does not yet independently prove a position isn't inside its freeze
window (see BUG_BACKLOG.md); a broker that enforces it will still reject
via `order_check`, which this module DOES honor.
"""

from __future__ import annotations

from dataclasses import dataclass

from adaptive_scalper.gateway.demo_gate import verify_demo_before_order
from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.gateway.retcodes import interpret_retcode
from adaptive_scalper.gateway.types import OrderAction, OrderCheckResult, OrderRequest, OrderSendResult
from adaptive_scalper.position_management.adaptive_exit import resolve_new_stop_price

NOT_DEMO = "NOT_DEMO"
ALREADY_CLOSED = "ALREADY_CLOSED"
VOLUME_MISMATCH = "VOLUME_MISMATCH"
TOO_CLOSE_TO_PRICE = "TOO_CLOSE_TO_PRICE"
NO_QUOTE = "NO_QUOTE"
NO_CHANGE = "NO_CHANGE"
BROKER_CONSTRAINT = "BROKER_CONSTRAINT"
UNKNOWN = "UNKNOWN"
REJECTED = "REJECTED"
SENT = "SENT"

DEFAULT_ORDER_CHECK_SUCCESS_RETCODES = frozenset({0, 10009})

_NO_STOP_SENTINEL = 0.0


@dataclass(frozen=True)
class StopModificationOutcome:
    status: str
    detail: str
    new_stop_price: float | None = None
    result: OrderSendResult | None = None


def _fresh_position(gateway: Gateway, broker_position_id: str):
    positions = {p.broker_position_id: p for p in gateway.positions_get()}
    return positions.get(str(broker_position_id))


def modify_protective_stop_safely(
    gateway: Gateway,
    *,
    broker_position_id: str,
    expected_direction: str,
    expected_volume: float,
    broker_symbol: str,
    proposed_stop_price: float,
    magic: int = 0,
    comment: str = "",
    order_check_success_retcodes: frozenset[int] = DEFAULT_ORDER_CHECK_SUCCESS_RETCODES,
) -> StopModificationOutcome:
    demo = verify_demo_before_order(gateway)
    if not demo.allowed:
        return StopModificationOutcome(NOT_DEMO, f"refusing a broker-mutating stop change: {demo.detail}")

    live = _fresh_position(gateway, broker_position_id)
    if live is None:
        return StopModificationOutcome(
            ALREADY_CLOSED,
            f"position {broker_position_id!r} is no longer reported by the broker — nothing to protect anymore",
        )
    if live.direction != expected_direction or abs(live.volume - expected_volume) > 1e-9:
        return StopModificationOutcome(
            VOLUME_MISMATCH,
            f"position {broker_position_id!r} broker state (direction={live.direction}, volume={live.volume}) "
            f"disagrees with expected (direction={expected_direction}, volume={expected_volume}) — refusing to send",
        )

    current_stop = live.stop_loss
    if current_stop in (None, _NO_STOP_SENTINEL):
        safe_stop = proposed_stop_price
    else:
        safe_stop = resolve_new_stop_price(current_stop, proposed_stop_price, live.direction)

    if current_stop not in (None, _NO_STOP_SENTINEL) and safe_stop == current_stop:
        return StopModificationOutcome(
            NO_CHANGE,
            f"proposed stop {proposed_stop_price} would not advance protection beyond the current "
            f"broker stop {current_stop} — no request sent",
            new_stop_price=current_stop,
        )

    symbol_spec = gateway.symbol_info(broker_symbol)
    if symbol_spec is None:
        return StopModificationOutcome(BROKER_CONSTRAINT, f"no symbol_info for {broker_symbol!r}")

    tick = gateway.symbol_info_tick(broker_symbol)
    if tick is None:
        return StopModificationOutcome(NO_QUOTE, f"no current quote for {broker_symbol!r}")

    min_distance = symbol_spec.trade_stops_level * symbol_spec.point
    reference_price = tick.bid if live.direction == "BUY" else tick.ask
    distance = (reference_price - safe_stop) if live.direction == "BUY" else (safe_stop - reference_price)
    if min_distance > 0 and distance < min_distance:
        return StopModificationOutcome(
            TOO_CLOSE_TO_PRICE,
            f"proposed stop {safe_stop} is {distance} from the current price, below the broker's "
            f"required minimum distance {min_distance} ({symbol_spec.trade_stops_level} points)",
        )

    request = OrderRequest(
        action=OrderAction.SLTP, symbol=broker_symbol, direction=live.direction, volume=0.0,
        position_ticket=int(broker_position_id), stop_loss=safe_stop, take_profit=live.take_profit,
        magic=magic, comment=comment,
    )

    check: OrderCheckResult = gateway.order_check(request)
    if check.retcode not in order_check_success_retcodes:
        return StopModificationOutcome(
            BROKER_CONSTRAINT, f"order_check failed: retcode={check.retcode} comment={check.comment!r}",
        )

    # Refresh again, independently, immediately before send.
    live2 = _fresh_position(gateway, broker_position_id)
    if live2 is None:
        return StopModificationOutcome(ALREADY_CLOSED, f"position {broker_position_id!r} closed before send")
    if live2.direction != expected_direction or abs(live2.volume - expected_volume) > 1e-9:
        return StopModificationOutcome(VOLUME_MISMATCH, "broker position state changed before send")
    if live2.stop_loss not in (None, _NO_STOP_SENTINEL):
        already_advanced = resolve_new_stop_price(live2.stop_loss, safe_stop, live2.direction) == live2.stop_loss
        if already_advanced:
            return StopModificationOutcome(
                NO_CHANGE, "broker stop already advanced to or beyond the proposed value before send",
                new_stop_price=live2.stop_loss,
            )

    result = gateway.order_send(request)
    interpretation = interpret_retcode(result.retcode)

    if interpretation.order_state.value == "UNKNOWN":
        return StopModificationOutcome(UNKNOWN, interpretation.detail, result=result)
    if interpretation.is_definitive_rejection:
        return StopModificationOutcome(REJECTED, interpretation.detail, result=result)
    return StopModificationOutcome(SENT, f"stop advanced to {safe_stop}", new_stop_price=safe_stop, result=result)
