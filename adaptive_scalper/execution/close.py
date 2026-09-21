"""Safe position close (execution-safety review round 1 finding #2,
round 2 finding #3; brought to `stop_modification.py`'s hardening
standard by external review 2026-09-21 findings #3/#4).

A close is NEVER simply "send an opposite DEAL" without identifying the
exact position — that risks creating opposite/unintended exposure,
especially on a hedging account where symbol+direction alone can't
disambiguate which position to reduce. `close_position_safely()` carries
the SAME two-IDENTICAL-rounds pattern `stop_modification.py` uses:

    ROUND 1 (before order_check):
        fresh DEMO -> fresh exact broker position -> fresh symbol_info
        -> prove broker_symbol == live.symbol -> fresh quote
        -> derive supported filling -> exact targeted-close OrderRequest
        -> order_check

    ROUND 2 (immediately before send, entirely re-fetched, never reused):
        fresh DEMO -> fresh exact position -> fresh symbol_info
        -> fresh quote -> fresh filling support -> prove position still
        exists -> prove direction/volume/symbol unchanged -> rebuild the
        request if anything changed -> order_check the REBUILT exact
        request if it differs from round 1's -> order_send

Every gateway call here (`terminal_info`/`account_info`/`positions_get`/
`symbol_info`/`symbol_info_tick`) is itself always a live, uncached call
on both `Mt5Gateway` and `FakeGateway` — there is no snapshot layer to go
stale between the two rounds, which is what makes calling these methods
twice (rather than needing an external evidence-provider callable, unlike
`execution.service.submit_new_entry`, which needs derived cross-subsystem
evidence a single gateway call can't produce) sufficient to satisfy the
"refresh immediately before send" requirement here.

**Result states are never collapsed into a blanket "sent" outcome**
(finding #4): only a broker retcode category of `DONE` is `FULLY_CLOSED`;
`DONE_PARTIAL` is `PARTIAL_CLOSE` (and immediately updates local
volume/risk from the REAL filled volume — never assumed to have fully
closed the position); `CANCELLED`/`PLACED`(->`RESTING`)/ambiguous
categories each get their own distinct, honest outcome — never silently
reported as success.

Per directive: a close must NOT be blocked merely because the NEW-ENTRY
kill switch is engaged — risk reduction stays available even when new
exposure is blocked. This module therefore never checks the kill switch
or the full final-permission gate; it checks only what actually matters
for a broker-mutating CLOSE to be safe: DEMO account truth, accurate
freshly-proven position identity, and quote freshness.
"""

from __future__ import annotations

import dataclasses
import sqlite3
import time
import typing
from dataclasses import dataclass
from typing import Callable

from adaptive_scalper.gateway.broker_constraints import derive_filling_type
from adaptive_scalper.gateway.demo_gate import verify_demo_before_order
from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.gateway.retcodes import RetcodeCategory, interpret_retcode
from adaptive_scalper.gateway.symbol_validation import DEFAULT_MAX_EXECUTION_QUOTE_AGE_SECONDS, validate_execution_quote
from adaptive_scalper.gateway.types import OrderAction, OrderCheckResult, OrderRequest, OrderSendResult

if typing.TYPE_CHECKING:
    from adaptive_scalper.execution.reconciliation import ReconciliationReport

NOT_DEMO = "NOT_DEMO"
ALREADY_CLOSED = "ALREADY_CLOSED"
VOLUME_MISMATCH = "VOLUME_MISMATCH"
SYMBOL_MISMATCH = "SYMBOL_MISMATCH"
BROKER_CONSTRAINT = "BROKER_CONSTRAINT"
NO_QUOTE = "NO_QUOTE"
UNKNOWN = "UNKNOWN"
REJECTED = "REJECTED"
# External review finding #4 (2026-09-21): explicit, distinct outcomes --
# never a blanket SENT hiding what the broker actually did.
CANCELLED = "CANCELLED"
RESTING = "RESTING"
PARTIAL_CLOSE = "PARTIAL_CLOSE"
FULLY_CLOSED = "FULLY_CLOSED"

# Outcomes reached ONLY after order_send() was actually called — a real
# request reached the broker (whether it filled, partially filled, was
# rejected/cancelled, or its outcome is uncertain). Every other status is
# a PRE-SEND block (DEMO verification, position/symbol/volume mismatch,
# order_check failure) where nothing was ever transmitted. Callers
# persisting request-timing state (external review finding #4, prior
# checkpoint) must use this to avoid recording a request timestamp for a
# request that never left the process.
POST_SEND_STATUSES = frozenset({FULLY_CLOSED, PARTIAL_CLOSE, RESTING, CANCELLED, REJECTED, UNKNOWN})
# The subset of POST_SEND_STATUSES that represents REAL broker exposure
# reduction -- what a caller should trigger reconciliation/exit-fill
# recording on. RESTING/CANCELLED/REJECTED/UNKNOWN reduced nothing.
REAL_EXPOSURE_CHANGE_STATUSES = frozenset({FULLY_CLOSED, PARTIAL_CLOSE, UNKNOWN})

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


@dataclass(frozen=True)
class _RoundResult:
    """One full round of fresh pre-send verification (mirrors
    `stop_modification.py`'s `_RoundResult`). `blocked` is a ready-to-
    return `CloseOutcome` when anything failed; the caller must stop and
    return it immediately without sending."""

    blocked: CloseOutcome | None
    live: object | None = None
    filling_type: str | None = None


def _resolve_round(
    gateway: Gateway,
    *,
    broker_position_id: str,
    expected_direction: str,
    expected_volume: float,
    broker_symbol: str,
    max_quote_age_seconds: float,
    clock: Callable[[], float],
) -> _RoundResult:
    """Fresh DEMO + fresh exact position + fresh symbol/filling/quote —
    entirely re-fetched, never reused from a prior round. Called once
    before `order_check` and again, identically, immediately before
    `order_send`. `clock()` is called HERE, freshly, every round
    (external review finding #1's fix applied consistently here too)."""
    demo = verify_demo_before_order(gateway)
    if not demo.allowed:
        return _RoundResult(CloseOutcome(NOT_DEMO, f"refusing a broker-mutating close: {demo.detail}"))

    live = _fresh_position(gateway, broker_position_id)
    if live is None:
        return _RoundResult(CloseOutcome(
            ALREADY_CLOSED,
            f"position {broker_position_id!r} is no longer reported by the broker — treat as already "
            f"closed and reconcile; refusing to send an opposite trade against a position that doesn't exist",
        ))

    if live.direction != expected_direction or abs(live.volume - expected_volume) > 1e-9:
        return _RoundResult(CloseOutcome(
            VOLUME_MISMATCH,
            f"position {broker_position_id!r} broker state (direction={live.direction}, volume={live.volume}) "
            f"disagrees with expected (direction={expected_direction}, volume={expected_volume}) — refusing to send",
        ))

    if live.symbol != broker_symbol:
        return _RoundResult(CloseOutcome(
            SYMBOL_MISMATCH,
            f"position {broker_position_id!r} broker-reported symbol {live.symbol!r} disagrees with "
            f"expected {broker_symbol!r} — refusing to send",
        ))

    symbol_spec = gateway.symbol_info(broker_symbol)
    if symbol_spec is None:
        return _RoundResult(CloseOutcome(BROKER_CONSTRAINT, f"no symbol_info for {broker_symbol!r}"))

    filling_type = derive_filling_type(symbol_spec.filling_mode)
    if filling_type is None:
        return _RoundResult(CloseOutcome(BROKER_CONSTRAINT, f"no broker-supported filling type for {broker_symbol!r}"))

    tick = gateway.symbol_info_tick(broker_symbol)
    quote_check = validate_execution_quote(tick, max_quote_age_seconds=max_quote_age_seconds, now=clock())
    if not quote_check.valid:
        return _RoundResult(CloseOutcome(
            NO_QUOTE, f"quote check failed for {broker_symbol!r}: {quote_check.reason} — {quote_check.detail}",
        ))

    return _RoundResult(None, live=live, filling_type=filling_type)


def _classify_close_result(interpretation, result: OrderSendResult, requested_volume: float) -> CloseOutcome:
    """External review finding #4: only positive broker proof determines
    the outcome -- never a blanket "anything non-rejection is SENT"."""
    if interpretation.is_definitive_rejection:
        return CloseOutcome(REJECTED, interpretation.detail, result=result)
    if interpretation.category == RetcodeCategory.CANCELLED:
        return CloseOutcome(CANCELLED, interpretation.detail, result=result)
    if interpretation.category == RetcodeCategory.PLACED:
        # Not expected for a market DEAL close, but handled explicitly
        # rather than folded into success if a broker ever reports it.
        return CloseOutcome(RESTING, interpretation.detail, result=result)
    if interpretation.category == RetcodeCategory.TIMEOUT_OR_ERROR:
        return CloseOutcome(UNKNOWN, interpretation.detail, result=result)
    if interpretation.category == RetcodeCategory.DONE_PARTIAL:
        return CloseOutcome(
            PARTIAL_CLOSE,
            f"partial close: volume_filled={result.volume_filled} of requested {requested_volume}",
            result=result,
        )
    if interpretation.category == RetcodeCategory.DONE:
        return CloseOutcome(FULLY_CLOSED, f"position fully closed: {interpretation.detail}", result=result)
    return CloseOutcome(UNKNOWN, interpretation.detail, result=result)  # defensive: never guess success


def _apply_partial_close_to_local_state(conn: sqlite3.Connection, broker_position_id: str, filled_volume: float) -> None:
    """On a REAL broker-confirmed partial close, update local
    volume/initial_monetary_risk from broker truth immediately — never
    left stale until some future reconciliation pass happens to notice
    (external review finding #4). Risk is scaled down by the same
    fraction the volume was reduced by (the per-unit risk rate implied by
    the position's own original volume/risk stays constant)."""
    row = conn.execute(
        "SELECT id, volume, initial_monetary_risk FROM positions WHERE broker_position_id = ? AND status = 'OPEN'",
        (str(broker_position_id),),
    ).fetchone()
    if row is None or row["volume"] <= 0:
        return  # nothing local to update -- reconciliation's own mismatch handling covers the rest
    remaining_volume = max(0.0, row["volume"] - filled_volume)
    remaining_fraction = remaining_volume / row["volume"]
    new_risk = row["initial_monetary_risk"] * remaining_fraction
    conn.execute(
        "UPDATE positions SET volume = ?, initial_monetary_risk = ? WHERE id = ?",
        (remaining_volume, new_risk, row["id"]),
    )
    conn.commit()


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
    max_quote_age_seconds: float = DEFAULT_MAX_EXECUTION_QUOTE_AGE_SECONDS,
    clock: Callable[[], float] = time.time,
    conn: sqlite3.Connection | None = None,
    reconciliation_chain_key: str | None = None,
) -> CloseOutcome:
    """`clock`: the FRESHNESS clock (external review finding #1) — called
    independently at each round; omit to use real `time.time()`.
    `conn`/`reconciliation_chain_key`: when both are supplied, a real
    reconciliation pass runs immediately after a real-exposure-changing
    outcome — pass neither in tests that don't need a DB."""
    round_kwargs = dict(
        broker_position_id=broker_position_id, expected_direction=expected_direction,
        expected_volume=expected_volume, broker_symbol=broker_symbol,
        max_quote_age_seconds=max_quote_age_seconds, clock=clock,
    )

    round1 = _resolve_round(gateway, **round_kwargs)
    if round1.blocked is not None:
        return round1.blocked

    close_direction = _opposite_direction(round1.live.direction)
    request = OrderRequest(
        action=OrderAction.DEAL, symbol=round1.live.symbol, direction=close_direction, volume=round1.live.volume,
        position_ticket=int(broker_position_id), magic=magic, comment=comment,
        deviation_points=deviation_points, filling_type=round1.filling_type,
    )

    check: OrderCheckResult = gateway.order_check(request)
    if check.retcode not in order_check_success_retcodes:
        return CloseOutcome(BROKER_CONSTRAINT, f"order_check failed: retcode={check.retcode} comment={check.comment!r}")

    # Second, INDEPENDENT round — identical to the first: fresh DEMO,
    # fresh position, fresh symbol/filling/quote — never reused from
    # round 1. A close that was valid 500ms ago may not be now.
    round2 = _resolve_round(gateway, **round_kwargs)
    if round2.blocked is not None:
        return round2.blocked

    close_direction2 = _opposite_direction(round2.live.direction)
    final_request = OrderRequest(
        action=OrderAction.DEAL, symbol=round2.live.symbol, direction=close_direction2, volume=round2.live.volume,
        position_ticket=int(broker_position_id), magic=magic, comment=comment,
        deviation_points=deviation_points, filling_type=round2.filling_type,
    )

    if final_request.filling_type != request.filling_type or final_request.volume != request.volume:
        # The request genuinely changed between rounds -- the SAME exact
        # request that will be sent must itself pass order_check.
        recheck: OrderCheckResult = gateway.order_check(final_request)
        if recheck.retcode not in order_check_success_retcodes:
            return CloseOutcome(
                BROKER_CONSTRAINT,
                f"order_check of the rebuilt request failed: retcode={recheck.retcode} comment={recheck.comment!r}",
            )

    result = gateway.order_send(final_request)
    interpretation = interpret_retcode(result.retcode)
    outcome = _classify_close_result(interpretation, result, final_request.volume)

    if conn is not None and outcome.status == PARTIAL_CLOSE and result.volume_filled:
        _apply_partial_close_to_local_state(conn, broker_position_id, result.volume_filled)

    if conn is not None and reconciliation_chain_key is not None and outcome.status in REAL_EXPOSURE_CHANGE_STATUSES:
        from adaptive_scalper.execution.reconciliation import run_reconciliation
        report = run_reconciliation(conn, gateway, reconciliation_chain_key)
        outcome = dataclasses.replace(outcome, reconciliation=report)

    return outcome
