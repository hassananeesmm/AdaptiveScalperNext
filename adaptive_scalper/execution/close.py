"""Safe position close (execution-safety review round 1 finding #2,
round 2 finding #3).

A close is NEVER simply "send an opposite DEAL" without identifying the
exact position — that risks creating opposite/unintended exposure,
especially on a hedging account where symbol+direction alone can't
disambiguate which position to reduce. `close_position_safely()` now
carries the SAME pre-send protections `execution/service.py` gives a new
entry:

    fresh DEMO verification (account/terminal, re-fetched — NOT cached)
    -> fresh positions_get() -> prove the exact ticket/direction/volume
    -> fresh symbol_info() -> derive a broker-supported filling type
    -> exact OrderRequest (a CLOSE, via `position_ticket`), built once
    -> order_check(EXACT request) -- mandatory, never skipped
    -> refresh DEMO verification AND positions_get() again, independently
    -> order_send(SAME exact request)
    -> interpret the retcode through gateway.retcodes.interpret_retcode()
       (never a bare success/reject binary)
    -> reconcile immediately when a DB connection is supplied

Every gateway call here (`terminal_info`/`account_info`/`positions_get`/
`symbol_info`) is itself always a live, uncached call on both
`Mt5Gateway` and `FakeGateway` — there is no snapshot layer to go stale
between the two rounds of checks this function makes, which is what
makes calling these methods twice (rather than needing an external
evidence-provider callable, unlike `execution.service.submit_new_entry`,
which needs derived cross-subsystem evidence a single gateway call can't
produce) sufficient to satisfy the "refresh immediately before send"
requirement here.

Per directive: a close must NOT be blocked merely because the NEW-ENTRY
kill switch is engaged — risk reduction stays available even when new
exposure is blocked. This module therefore never checks the kill switch
or the full final-permission gate; it checks only what actually matters
for a broker-mutating CLOSE to be safe: DEMO account truth and accurate,
freshly-proven position identity.
"""

from __future__ import annotations

import dataclasses
import sqlite3
import typing
from dataclasses import dataclass

from adaptive_scalper.gateway.broker_constraints import derive_filling_type
from adaptive_scalper.gateway.demo_gate import verify_demo_before_order
from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.gateway.retcodes import interpret_retcode
from adaptive_scalper.gateway.types import OrderAction, OrderCheckResult, OrderRequest, OrderSendResult

if typing.TYPE_CHECKING:
    from adaptive_scalper.execution.reconciliation import ReconciliationReport

NOT_DEMO = "NOT_DEMO"
ALREADY_CLOSED = "ALREADY_CLOSED"
VOLUME_MISMATCH = "VOLUME_MISMATCH"
BROKER_CONSTRAINT = "BROKER_CONSTRAINT"
UNKNOWN = "UNKNOWN"
REJECTED = "REJECTED"
SENT = "SENT"

# Outcomes reached ONLY after order_send() was actually called — a real
# request reached the broker (whether it filled, was rejected, or its
# outcome is uncertain). Every other status is a PRE-SEND block (DEMO
# verification, position/volume mismatch, order_check failure) where
# nothing was ever transmitted. Callers persisting request-timing state
# (external review finding #4) must use this to avoid recording a
# request timestamp for a request that never left the process.
POST_SEND_STATUSES = frozenset({SENT, REJECTED, UNKNOWN})

DEFAULT_ORDER_CHECK_SUCCESS_RETCODES = frozenset({0, 10009})


@dataclass(frozen=True)
class CloseOutcome:
    status: str
    detail: str
    result: OrderSendResult | None = None
    reconciliation: "ReconciliationReport | None" = None


def _opposite_direction(direction: str) -> str:
    if direction == "BUY":
        return "SELL"
    if direction == "SELL":
        return "BUY"
    raise ValueError(f"direction must be 'BUY' or 'SELL', got {direction!r}")


def _fresh_position(gateway: Gateway, broker_position_id: str):
    positions = {p.broker_position_id: p for p in gateway.positions_get()}
    return positions.get(str(broker_position_id))


def _verify_and_fetch_position(gateway: Gateway, broker_position_id: str, expected_direction: str, expected_volume: float):
    """One round of the two identical rounds of checks this function
    performs (before order_check, and again before order_send). Returns
    `(outcome_or_None, live_position_or_None)` — a non-`None` outcome
    means the caller must stop and return it immediately, never sending."""
    demo = verify_demo_before_order(gateway)
    if not demo.allowed:
        return CloseOutcome(NOT_DEMO, f"refusing a broker-mutating close: {demo.detail}"), None

    live = _fresh_position(gateway, broker_position_id)
    if live is None:
        return CloseOutcome(
            ALREADY_CLOSED,
            f"position {broker_position_id!r} is no longer reported by the broker — treat as already "
            f"closed and reconcile; refusing to send an opposite trade against a position that doesn't exist",
        ), None

    if live.direction != expected_direction or abs(live.volume - expected_volume) > 1e-9:
        return CloseOutcome(
            VOLUME_MISMATCH,
            f"position {broker_position_id!r} broker state (direction={live.direction}, volume={live.volume}) "
            f"disagrees with expected (direction={expected_direction}, volume={expected_volume}) — refusing to send",
        ), None

    return None, live


def close_position_safely(
    gateway: Gateway,
    *,
    broker_position_id: str,
    expected_direction: str,
    expected_volume: float,
    broker_symbol: str,
    magic: int = 0,
    comment: str = "",
    deviation_points: int = 20,
    order_check_success_retcodes: frozenset[int] = DEFAULT_ORDER_CHECK_SUCCESS_RETCODES,
    conn: sqlite3.Connection | None = None,
    reconciliation_chain_key: str | None = None,
) -> CloseOutcome:
    """`conn`/`reconciliation_chain_key`: when both are supplied, a real
    reconciliation pass runs immediately after a `SENT` outcome — pass
    neither in tests that don't need a DB."""
    blocked, live = _verify_and_fetch_position(gateway, broker_position_id, expected_direction, expected_volume)
    if blocked is not None:
        return blocked

    filling_type = derive_filling_type(gateway.symbol_info(broker_symbol).filling_mode if gateway.symbol_info(broker_symbol) else 0)
    if filling_type is None:
        return CloseOutcome(BROKER_CONSTRAINT, f"no broker-supported filling type for {broker_symbol!r}")

    close_direction = _opposite_direction(live.direction)
    request = OrderRequest(
        action=OrderAction.DEAL, symbol=live.symbol, direction=close_direction, volume=live.volume,
        position_ticket=int(broker_position_id), magic=magic, comment=comment,
        deviation_points=deviation_points, filling_type=filling_type,
    )

    check: OrderCheckResult = gateway.order_check(request)
    if check.retcode not in order_check_success_retcodes:
        return CloseOutcome(BROKER_CONSTRAINT, f"order_check failed: retcode={check.retcode} comment={check.comment!r}")

    # Refresh EVERYTHING again, independently, immediately before send.
    blocked, live = _verify_and_fetch_position(gateway, broker_position_id, expected_direction, expected_volume)
    if blocked is not None:
        return blocked

    result = gateway.order_send(request)
    interpretation = interpret_retcode(result.retcode)

    if interpretation.order_state.value == "UNKNOWN":
        outcome = CloseOutcome(UNKNOWN, interpretation.detail, result=result)
    elif interpretation.is_definitive_rejection:
        outcome = CloseOutcome(REJECTED, interpretation.detail, result=result)
    else:
        outcome = CloseOutcome(SENT, f"close request sent for position {broker_position_id!r}: {interpretation.detail}", result=result)

    if conn is not None and reconciliation_chain_key is not None:
        from adaptive_scalper.execution.reconciliation import run_reconciliation
        report = run_reconciliation(conn, gateway, reconciliation_chain_key)
        outcome = dataclasses.replace(outcome, reconciliation=report)

    return outcome
