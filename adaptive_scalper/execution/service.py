"""THE single execution orchestration service (execution-safety review
findings #7, #8, #9).

This is the ONLY module in the codebase permitted to call
`Gateway.order_send()` for a NEW entry (enforced by
`tests/test_architecture_execution_boundary.py`, which scans
`strategies/`, `learning/`, `rag/`, and `dashboard/` for direct calls and
fails the build if it finds one). Strategies, the selector, RAG, ML, and
the dashboard produce PROPOSALS; only this module turns an ALLOWed
proposal into a broker order.

Flow (directive's execution flow, finding #8/#9):

    idempotent PROPOSED order
    -> FRESH evaluate_and_journal_final_permission (re-run in full, every
       single call — no cached ALLOW is ever reused across calls)
    -> resolve a broker-supported filling type from the symbol spec
       (BLOCK_BROKER_CONSTRAINT if none is supported)
    -> exact OrderRequest, built ONCE and never mutated afterward
    -> order_check() against that EXACT request (mandatory, never skipped)
    -> SUBMITTED
    -> order_send() of that SAME exact request
    -> interpret the result through the state machine (never conflating
       broker acknowledgement with a fill) and resolve the true broker
       position id via execution.position_resolution (never invented)
    -> journal every step

`permission_input` must be constructed by the CALLER from genuinely
fresh evidence immediately before calling this function (fresh
account_info/terminal_info/symbol_info/tick, current reconciliation
status, current UNKNOWN state, current correlation/risk/portfolio
snapshot) — this module does not cache or reuse a prior evaluation, and
neither may the caller; a `FinalPermissionInput` built even a few seconds
earlier and reused across calls defeats finding #9 entirely.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from adaptive_scalper.core.final_permission import ALLOW as _PERMISSION_ALLOW
from adaptive_scalper.core.final_permission import FinalPermissionInput, evaluate_and_journal_final_permission
from adaptive_scalper.execution.position_resolution import resolve_opened_position_id
from adaptive_scalper.execution.state_machine import OrderState
from adaptive_scalper.execution.store import OrderRecord, create_order, transition_order_state
from adaptive_scalper.gateway.broker_constraints import derive_filling_type
from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.gateway.types import OrderAction, OrderRequest, SymbolSpec
from adaptive_scalper.journal.events import append_event

BLOCK_MARGIN = "BLOCK_MARGIN"
BLOCK_BROKER_CONSTRAINT = "BLOCK_BROKER_CONSTRAINT"

BLOCKED_PERMISSION = "BLOCKED_PERMISSION"
BLOCKED_BROKER_CONSTRAINT = "BLOCKED_BROKER_CONSTRAINT"
BLOCKED_MARGIN = "BLOCKED_MARGIN"
FILLED = "FILLED"
UNKNOWN = "UNKNOWN"
REJECTED = "REJECTED"

# order_check success is checked leniently against BOTH conventions seen
# across MT5 broker implementations (0 == "no error", and the same
# TRADE_RETCODE_DONE=10009 used for a successful order_send) — this has
# not yet been live-verified against the real DEMO terminal's actual
# order_check() return value, and is a named gap until it is (see
# BUG_BACKLOG.md); callers may override via `order_check_success_retcodes`.
DEFAULT_ORDER_CHECK_SUCCESS_RETCODES = frozenset({0, 10009})
# Real MT5's TRADE_RETCODE_DONE for a successful order_send.
DEFAULT_ORDER_SEND_SUCCESS_RETCODES = frozenset({10009})


@dataclass(frozen=True)
class SubmissionOutcome:
    status: str
    order: OrderRecord
    detail: str


def submit_new_entry(
    conn,
    gateway: Gateway,
    *,
    chain_key: str,
    client_request_id: str,
    canonical_symbol: str,
    broker_symbol: str,
    direction: str,
    volume: float,
    stop_loss: float | None,
    take_profit: float | None,
    symbol_spec: SymbolSpec,
    permission_input: FinalPermissionInput,
    magic: int = 0,
    comment: str = "",
    deviation_points: int = 20,
    available_margin_free: float | None = None,
    history_window_seconds: int = 300,
    order_check_success_retcodes: frozenset[int] = DEFAULT_ORDER_CHECK_SUCCESS_RETCODES,
    order_send_success_retcodes: frozenset[int] = DEFAULT_ORDER_SEND_SUCCESS_RETCODES,
    now_utc: int | None = None,
) -> SubmissionOutcome:
    now = now_utc if now_utc is not None else int(time.time())

    order = create_order(
        conn, client_request_id, canonical_symbol, broker_symbol, direction, volume,
        chain_key=chain_key, stop_loss=stop_loss, take_profit=take_profit, now_utc=now,
    )
    if order.state != OrderState.PROPOSED:
        # An exact retry of an already-progressed order — do not re-submit.
        return SubmissionOutcome(
            order.state.value, order,
            f"order already exists in state={order.state.value}; not re-evaluating or re-sending",
        )

    permission = evaluate_and_journal_final_permission(conn, chain_key, permission_input, now_utc=now)
    if permission.decision != _PERMISSION_ALLOW:
        return SubmissionOutcome(BLOCKED_PERMISSION, order, permission.reason)

    filling_type = derive_filling_type(symbol_spec.filling_mode)
    if filling_type is None:
        detail = (
            f"no broker-supported filling type for {broker_symbol!r} "
            f"(filling_mode bitmask={symbol_spec.filling_mode})"
        )
        append_event(
            conn, chain_key, "ENTRY_BLOCKED", now, canonical_symbol,
            {"decision": BLOCK_BROKER_CONSTRAINT, "reason": detail}, strategy_key=None,
        )
        return SubmissionOutcome(BLOCKED_BROKER_CONSTRAINT, order, detail)

    request = OrderRequest(
        action=OrderAction.DEAL, symbol=broker_symbol, direction=direction, volume=volume,
        stop_loss=stop_loss, take_profit=take_profit, deviation_points=deviation_points,
        magic=magic, comment=comment, filling_type=filling_type,
    )

    check = gateway.order_check(request)
    if check.retcode not in order_check_success_retcodes:
        detail = f"order_check failed: retcode={check.retcode} comment={check.comment!r}"
        append_event(
            conn, chain_key, "ENTRY_BLOCKED", now, canonical_symbol,
            {"decision": BLOCK_BROKER_CONSTRAINT, "reason": detail}, strategy_key=None,
        )
        return SubmissionOutcome(BLOCKED_BROKER_CONSTRAINT, order, detail)

    if (
        available_margin_free is not None
        and check.margin_required is not None
        and check.margin_required > available_margin_free
    ):
        detail = (
            f"order_check margin_required={check.margin_required:.2f} exceeds available "
            f"margin_free={available_margin_free:.2f}"
        )
        append_event(
            conn, chain_key, "ENTRY_BLOCKED", now, canonical_symbol,
            {"decision": BLOCK_MARGIN, "reason": detail}, strategy_key=None,
        )
        return SubmissionOutcome(BLOCKED_MARGIN, order, detail)

    order = transition_order_state(conn, order.id, OrderState.SUBMITTED, detail="order_check passed", now_utc=now)
    append_event(conn, chain_key, "ORDER_SUBMITTED", now, canonical_symbol, {"request": "DEAL"}, strategy_key=None)

    result = gateway.order_send(request)

    if result.retcode not in order_send_success_retcodes:
        order = transition_order_state(
            conn, order.id, OrderState.REJECTED, detail=f"order_send rejected: {result.comment}",
            broker_order_id=result.broker_order_id, broker_retcode=result.retcode,
            broker_comment=result.comment, raw_broker_response=result.raw, now_utc=now,
        )
        append_event(
            conn, chain_key, "ORDER_REJECTED", now, canonical_symbol,
            {"retcode": result.retcode, "comment": result.comment}, strategy_key=None,
            broker_order_id=result.broker_order_id,
        )
        return SubmissionOutcome(REJECTED, order, f"broker rejected the order: {result.comment}")

    order = transition_order_state(
        conn, order.id, OrderState.ACCEPTED, detail="order_send acknowledged",
        broker_order_id=result.broker_order_id, broker_retcode=result.retcode,
        broker_comment=result.comment, raw_broker_response=result.raw, now_utc=now,
    )
    append_event(
        conn, chain_key, "ORDER_ACCEPTED", now, canonical_symbol,
        {"retcode": result.retcode}, strategy_key=None, broker_order_id=result.broker_order_id,
    )

    resolution = resolve_opened_position_id(
        gateway, broker_deal_id=result.broker_deal_id, broker_order_id=result.broker_order_id,
        window_from_utc=now - history_window_seconds, window_to_utc=now + history_window_seconds,
    )
    if not resolution.resolved:
        order = transition_order_state(
            conn, order.id, OrderState.UNKNOWN, detail=resolution.detail, now_utc=now,
        )
        append_event(
            conn, chain_key, "ORDER_UNKNOWN", now, canonical_symbol,
            {"detail": resolution.detail}, strategy_key=None, broker_order_id=result.broker_order_id,
        )
        from adaptive_scalper.execution.reconciliation import record_incident
        record_incident(conn, "UNKNOWN_OUTCOME", resolution.detail, order_id=order.id, now_utc=now)
        return SubmissionOutcome(UNKNOWN, order, resolution.detail)

    order = transition_order_state(
        conn, order.id, OrderState.FILLED, detail="position resolved from broker history",
        broker_position_id=resolution.broker_position_id, now_utc=now,
    )
    append_event(
        conn, chain_key, "ORDER_FILLED", now, canonical_symbol,
        {"volume_filled": result.volume_filled, "price_filled": result.price_filled}, strategy_key=None,
        broker_order_id=result.broker_order_id, broker_position_id=resolution.broker_position_id,
        broker_deal_id=result.broker_deal_id,
    )
    append_event(
        conn, chain_key, "POSITION_OPENED", now, canonical_symbol,
        {"volume": result.volume_filled, "price": result.price_filled}, strategy_key=None,
        broker_position_id=resolution.broker_position_id,
    )
    return SubmissionOutcome(FILLED, order, f"filled and resolved to position {resolution.broker_position_id}")
