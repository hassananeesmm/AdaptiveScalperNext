"""Safe protective-stop modification (directive: "Protective Stop
Execution", directive section 23).

Broker SL/TP remains the hard, always-on protection; this module's only
job is ADVANCING it — never loosening it, and never touching it when the
outcome is uncertain. It follows the same two-round-of-fresh-checks
pattern `execution/close.py` established: fresh DEMO verification and
fresh broker position state, once before `order_check`, and again
immediately before `order_send`.

**Both rounds are IDENTICAL in what they re-verify** (external review
finding, 2026-09-21): a first draft of this module verified DEMO only in
round 1 and reused round 1's `symbol_info()`/`symbol_info_tick()` and the
FIRST position snapshot's `take_profit` when building the request sent in
round 2. That is unsafe on three counts, all fixed here:

1. DEMO must be reverified immediately before `order_send`, not only
   before `order_check` — an account/terminal state change between the
   two calls (kill switch aside; this module doesn't own that) must
   still block the send. `_resolve_round()` below calls
   `verify_demo_before_order()` on every round, including the final one.
2. The take-profit value sent must come from the FRESH round-2 position
   snapshot, never round 1's — if TP changed between `order_check` and
   `order_send` (broker-side trail, manual intervention, another
   process), sending round 1's stale TP would silently overwrite a newer
   value the operator/broker already set.
3. `symbol_info()`/`symbol_info_tick()` (trade mode, stops level, freeze
   level, point, quote freshness/distance-from-price) are refetched and
   revalidated in round 2 too — a symbol that was tradable and a
   distance that was safe 500ms ago may no longer be.

If round 2's freshly rebuilt request differs from round 1's (a changed
`take_profit`, or a re-resolved `stop_loss` because the broker's stop
moved between rounds), `order_check` is run AGAIN against the exact new
request before `order_send` — never sending a request that was checked
in a different shape than the one actually submitted.

Monotonic guarantee (directive section 23 — reused, not reimplemented:
`position_management.adaptive_exit.resolve_new_stop_price()` is the
single source of truth for "never move a protective stop backward," used
here exactly as `adaptive_exit` uses it when deciding a new stop value).
If the broker's CURRENT stop has already reached or passed the proposed
one (by this module's own request, a broker-side trail, or any other
process), this module sends NOTHING — `NO_CHANGE`, never a request that
could only ever worsen or no-op the position's protection.

Respects `SymbolSpec.trade_stops_level` (minimum broker-required distance
from the current price, in points) AND `SymbolSpec.trade_freeze_level`
(minimum distance within which the broker refuses to modify an existing
order/position at all) — refusing a too-close or frozen stop locally
rather than discovering it only via a failed `order_check`/`order_send`.
A broker that enforces either will also reject via `order_check`, which
this module DOES honor as defense in depth.
"""

from __future__ import annotations

from dataclasses import dataclass

from adaptive_scalper.gateway.demo_gate import verify_demo_before_order
from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.gateway.retcodes import interpret_retcode
from adaptive_scalper.gateway.symbol_validation import (
    DEFAULT_MAX_EXECUTION_QUOTE_AGE_SECONDS,
    validate_execution_quote,
)
from adaptive_scalper.gateway.types import (
    OrderAction,
    OrderCheckResult,
    OrderRequest,
    OrderSendResult,
    SymbolTradeMode,
)
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


@dataclass(frozen=True)
class _RoundResult:
    """One full round of fresh pre-send verification. `blocked` is a
    ready-to-return `StopModificationOutcome` when anything failed; the
    caller must stop and return it immediately without sending. When
    `blocked` is None, every other field is populated with fresh,
    just-fetched state safe to build (or rebuild) a request from."""

    blocked: StopModificationOutcome | None
    live: object | None = None
    safe_stop: float | None = None
    take_profit: float | None = None


def _resolve_round(
    gateway: Gateway,
    *,
    broker_position_id: str,
    expected_direction: str,
    expected_volume: float,
    broker_symbol: str,
    proposed_stop_price: float,
    max_quote_age_seconds: float,
    now: float | None,
) -> _RoundResult:
    """Fresh DEMO verification + fresh position + fresh symbol/tick state,
    entirely re-fetched and re-validated — never reused from a prior
    round. Called once before `order_check` and again, identically,
    immediately before `order_send`."""
    demo = verify_demo_before_order(gateway)
    if not demo.allowed:
        return _RoundResult(StopModificationOutcome(NOT_DEMO, f"refusing a broker-mutating stop change: {demo.detail}"))

    live = _fresh_position(gateway, broker_position_id)
    if live is None:
        return _RoundResult(StopModificationOutcome(
            ALREADY_CLOSED,
            f"position {broker_position_id!r} is no longer reported by the broker — nothing to protect anymore",
        ))
    if live.direction != expected_direction or abs(live.volume - expected_volume) > 1e-9:
        return _RoundResult(StopModificationOutcome(
            VOLUME_MISMATCH,
            f"position {broker_position_id!r} broker state (direction={live.direction}, volume={live.volume}) "
            f"disagrees with expected (direction={expected_direction}, volume={expected_volume}) — refusing to send",
        ))

    current_stop = live.stop_loss
    if current_stop in (None, _NO_STOP_SENTINEL):
        safe_stop = proposed_stop_price
    else:
        safe_stop = resolve_new_stop_price(current_stop, proposed_stop_price, live.direction)

    if current_stop not in (None, _NO_STOP_SENTINEL) and safe_stop == current_stop:
        return _RoundResult(StopModificationOutcome(
            NO_CHANGE,
            f"proposed stop {proposed_stop_price} would not advance protection beyond the current "
            f"broker stop {current_stop} — no request sent",
            new_stop_price=current_stop,
        ))

    symbol_spec = gateway.symbol_info(broker_symbol)
    if symbol_spec is None:
        return _RoundResult(StopModificationOutcome(BROKER_CONSTRAINT, f"no symbol_info for {broker_symbol!r}"))

    if symbol_spec.trade_mode == SymbolTradeMode.DISABLED:
        return _RoundResult(StopModificationOutcome(
            BROKER_CONSTRAINT, f"symbol {broker_symbol!r} trade_mode is DISABLED — refusing to modify",
        ))

    tick = gateway.symbol_info_tick(broker_symbol)
    quote_check = validate_execution_quote(tick, max_quote_age_seconds=max_quote_age_seconds, now=now)
    if not quote_check.valid:
        return _RoundResult(StopModificationOutcome(
            NO_QUOTE, f"quote check failed for {broker_symbol!r}: {quote_check.reason} — {quote_check.detail}",
        ))

    min_distance = symbol_spec.trade_stops_level * symbol_spec.point
    freeze_distance = symbol_spec.trade_freeze_level * symbol_spec.point
    required_distance = max(min_distance, freeze_distance)
    reference_price = tick.bid if live.direction == "BUY" else tick.ask
    distance = (reference_price - safe_stop) if live.direction == "BUY" else (safe_stop - reference_price)
    if required_distance > 0 and distance < required_distance:
        return _RoundResult(StopModificationOutcome(
            TOO_CLOSE_TO_PRICE,
            f"proposed stop {safe_stop} is {distance} from the current price, below the broker's "
            f"required minimum distance {required_distance} (stops_level={symbol_spec.trade_stops_level}, "
            f"freeze_level={symbol_spec.trade_freeze_level} points)",
        ))

    return _RoundResult(None, live=live, safe_stop=safe_stop, take_profit=live.take_profit)


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
    max_quote_age_seconds: float = DEFAULT_MAX_EXECUTION_QUOTE_AGE_SECONDS,
    now: float | None = None,
) -> StopModificationOutcome:
    """`now`: epoch seconds to compare quote freshness against, for
    deterministic tests; omit to use real `time.time()`."""
    round_kwargs = dict(
        broker_position_id=broker_position_id,
        expected_direction=expected_direction,
        expected_volume=expected_volume,
        broker_symbol=broker_symbol,
        proposed_stop_price=proposed_stop_price,
        max_quote_age_seconds=max_quote_age_seconds,
        now=now,
    )

    round1 = _resolve_round(gateway, **round_kwargs)
    if round1.blocked is not None:
        return round1.blocked

    request = OrderRequest(
        action=OrderAction.SLTP, symbol=broker_symbol, direction=round1.live.direction, volume=0.0,
        position_ticket=int(broker_position_id), stop_loss=round1.safe_stop, take_profit=round1.take_profit,
        magic=magic, comment=comment,
    )

    check: OrderCheckResult = gateway.order_check(request)
    if check.retcode not in order_check_success_retcodes:
        return StopModificationOutcome(
            BROKER_CONSTRAINT, f"order_check failed: retcode={check.retcode} comment={check.comment!r}",
        )

    # Second, INDEPENDENT round — identical to the first: fresh DEMO,
    # fresh position, fresh symbol/tick/stops/freeze/distance — never
    # reused from round 1. A stop that was valid 500ms ago may not be now.
    round2 = _resolve_round(gateway, **round_kwargs)
    if round2.blocked is not None:
        return round2.blocked

    final_request = OrderRequest(
        action=OrderAction.SLTP, symbol=broker_symbol, direction=round2.live.direction, volume=0.0,
        position_ticket=int(broker_position_id), stop_loss=round2.safe_stop, take_profit=round2.take_profit,
        magic=magic, comment=comment,
    )

    if final_request.stop_loss != request.stop_loss or final_request.take_profit != request.take_profit:
        # The request genuinely changed between rounds (broker-side stop
        # movement, a newer TP) — the SAME exact request that will be sent
        # must itself pass order_check; the round-1 check does not cover it.
        recheck: OrderCheckResult = gateway.order_check(final_request)
        if recheck.retcode not in order_check_success_retcodes:
            return StopModificationOutcome(
                BROKER_CONSTRAINT,
                f"order_check of the rebuilt request failed: retcode={recheck.retcode} comment={recheck.comment!r}",
            )

    result = gateway.order_send(final_request)
    interpretation = interpret_retcode(result.retcode)

    if interpretation.order_state.value == "UNKNOWN":
        return StopModificationOutcome(UNKNOWN, interpretation.detail, result=result)
    if interpretation.is_definitive_rejection:
        return StopModificationOutcome(REJECTED, interpretation.detail, result=result)
    return StopModificationOutcome(
        SENT, f"stop advanced to {final_request.stop_loss}", new_stop_price=final_request.stop_loss, result=result,
    )
