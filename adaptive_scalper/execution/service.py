"""THE single execution orchestration service (execution-safety review
findings #1, #2, #7, #8, #9 from the first round; finding #1, #2 from
the second round).

This is the ONLY module in the codebase permitted to call
`Gateway.order_send()` for a NEW entry (enforced by
`tests/test_architecture_execution_boundary.py`, which scans
`strategies/`, `learning/`, `rag/`, and `dashboard/` for direct calls and
fails the build if it finds one). Strategies, the selector, RAG, ML, and
the dashboard produce PROPOSALS; only this module turns an ALLOWed
proposal into a broker order.

Flow (directive's execution flow; second-round finding #1 hardened it):

    idempotent PROPOSED order
    -> fetch_fresh_evidence() #1 (account/terminal/symbol/tick/kill-switch
       /reconciliation/UNKNOWN/duplicate/news/cost/correlation/risk/
       re-entry — everything final permission needs, gathered live)
    -> FRESH evaluate_and_journal_final_permission on that evidence
    -> resolve a broker-supported filling type from the fresh symbol spec
       (BLOCK_BROKER_CONSTRAINT if none is supported)
    -> exact OrderRequest, built ONCE and never mutated afterward
    -> order_check() against that EXACT request (mandatory, never skipped)
    -> fetch_fresh_evidence() #2 -- a SECOND, independent call, immediately
       before send
    -> FRESH evaluate_and_journal_final_permission AGAIN on the new
       evidence -- if anything volatile changed (kill switch engaged,
       account no longer DEMO, quote went stale, reconciliation became
       blocking, a duplicate/UNKNOWN appeared, risk/portfolio state
       moved), this call returns something other than ALLOW and
       order_send is NEVER reached
    -> SUBMITTED
    -> order_send() of that SAME exact request object
    -> interpret the raw retcode through gateway.retcodes.interpret_retcode()
       (never a bare "10009 or REJECTED" comparison -- a DONE_PARTIAL or
       PLACED result is real broker state, not a rejection) and resolve
       the true broker position id via execution.position_resolution
       (never invented)
    -> journal every step

Why a CALLABLE (`fetch_fresh_evidence: Callable[[], FreshEvidence]`)
instead of a pre-built `FinalPermissionInput` parameter: a plain
parameter can only ever be evaluated ONCE, at whatever moment the caller
happened to build it -- there is no way for this function to force a
SECOND, independently-fresh evaluation out of a value that was already
computed before this function was even called. Requiring a zero-argument
callable makes the "refresh immediately before send" step structural:
this function controls exactly when `fetch_fresh_evidence()` runs (twice,
with real work — an order_check — in between), and a caller cannot
satisfy that contract by handing over a cached snapshot no matter how
it's disguised. `FreshEvidence` bundles everything volatile finding #1
named: the `FinalPermissionInput` (which itself carries kill-switch/
reconciliation/UNKNOWN/duplicate/news/cost/correlation/risk/re-entry
state) plus the current `SymbolSpec` (account/terminal/symbol/tick are
the caller's responsibility to have refreshed into these two objects
immediately before each `fetch_fresh_evidence()` call — this function
has no gateway-polling logic of its own beyond `order_check`/`order_send`
themselves, keeping it a pure orchestrator over evidence its caller
supplies, exactly like every other gate in this codebase).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from adaptive_scalper.core.final_permission import ALLOW as _PERMISSION_ALLOW
from adaptive_scalper.core.final_permission import FinalPermissionInput, evaluate_and_journal_final_permission
from adaptive_scalper.execution.position_resolution import resolve_opened_position_id
from adaptive_scalper.execution.request_token import embed_request_token
from adaptive_scalper.execution.state_machine import OrderState
from adaptive_scalper.execution.store import OrderRecord, create_order, transition_order_state
from adaptive_scalper.gateway.broker_constraints import derive_filling_type
from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.gateway.retcodes import interpret_retcode
from adaptive_scalper.gateway.types import OrderAction, OrderRequest, SymbolSpec
from adaptive_scalper.journal.events import append_event

BLOCK_MARGIN = "BLOCK_MARGIN"
BLOCK_BROKER_CONSTRAINT = "BLOCK_BROKER_CONSTRAINT"

BLOCKED_PERMISSION = "BLOCKED_PERMISSION"
BLOCKED_PRESEND_RECHECK = "BLOCKED_PRESEND_RECHECK"
BLOCKED_BROKER_CONSTRAINT = "BLOCKED_BROKER_CONSTRAINT"
BLOCKED_MARGIN = "BLOCKED_MARGIN"
FILLED = "FILLED"
PARTIAL = "PARTIAL"
RESTING = "RESTING"
CANCELLED = "CANCELLED"
UNKNOWN = "UNKNOWN"
REJECTED = "REJECTED"

# order_check success is checked leniently against BOTH conventions seen
# across MT5 broker implementations (0 == "no error", and the same
# TRADE_RETCODE_DONE=10009 used for a successful order_send) — this has
# not yet been live-verified against the real DEMO terminal's actual
# order_check() return value, and is a named gap until it is (see
# BUG_BACKLOG.md); callers may override via `order_check_success_retcodes`.
DEFAULT_ORDER_CHECK_SUCCESS_RETCODES = frozenset({0, 10009})


@dataclass(frozen=True)
class FreshEvidence:
    """Everything `submit_new_entry()` needs that can change between two
    calls made moments apart — the whole point of finding #1's fix is
    that this bundle is rebuilt from scratch (not cached) at both call
    sites."""

    permission_input: FinalPermissionInput
    symbol_spec: SymbolSpec
    available_margin_free: float | None = None


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
    fetch_fresh_evidence: Callable[[], FreshEvidence],
    magic: int = 0,
    comment: str = "",
    deviation_points: int = 20,
    history_window_seconds: int = 300,
    order_check_success_retcodes: frozenset[int] = DEFAULT_ORDER_CHECK_SUCCESS_RETCODES,
    now_utc: int | None = None,
) -> SubmissionOutcome:
    now = now_utc if now_utc is not None else int(time.time())

    order = create_order(
        conn, client_request_id, canonical_symbol, broker_symbol, direction, volume,
        chain_key=chain_key, stop_loss=stop_loss, take_profit=take_profit, now_utc=now,
    )
    if order.state != OrderState.PROPOSED:
        # An exact retry of an already-progressed order — do not re-evaluate or re-send.
        return SubmissionOutcome(
            order.state.value, order,
            f"order already exists in state={order.state.value}; not re-evaluating or re-sending",
        )

    evidence = fetch_fresh_evidence()
    permission = evaluate_and_journal_final_permission(conn, chain_key, evidence.permission_input, now_utc=now)
    if permission.decision != _PERMISSION_ALLOW:
        return SubmissionOutcome(BLOCKED_PERMISSION, order, permission.reason)

    filling_type = derive_filling_type(evidence.symbol_spec.filling_mode)
    if filling_type is None:
        detail = (
            f"no broker-supported filling type for {broker_symbol!r} "
            f"(filling_mode bitmask={evidence.symbol_spec.filling_mode})"
        )
        append_event(
            conn, chain_key, "ENTRY_BLOCKED", now, canonical_symbol,
            {"decision": BLOCK_BROKER_CONSTRAINT, "reason": detail}, strategy_key=None,
        )
        return SubmissionOutcome(BLOCKED_BROKER_CONSTRAINT, order, detail)

    request = OrderRequest(
        action=OrderAction.DEAL, symbol=broker_symbol, direction=direction, volume=volume,
        stop_loss=stop_loss, take_profit=take_profit, deviation_points=deviation_points,
        magic=magic, comment=embed_request_token(client_request_id, comment), filling_type=filling_type,
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
        evidence.available_margin_free is not None
        and check.margin_required is not None
        and check.margin_required > evidence.available_margin_free
    ):
        detail = (
            f"order_check margin_required={check.margin_required:.2f} exceeds available "
            f"margin_free={evidence.available_margin_free:.2f}"
        )
        append_event(
            conn, chain_key, "ENTRY_BLOCKED", now, canonical_symbol,
            {"decision": BLOCK_MARGIN, "reason": detail}, strategy_key=None,
        )
        return SubmissionOutcome(BLOCKED_MARGIN, order, detail)

    # --- finding #1: refresh ALL volatile evidence again, independently,
    # immediately before send. No cached ALLOW from above is reused. ---
    fresh_evidence = fetch_fresh_evidence()
    presend_permission = evaluate_and_journal_final_permission(
        conn, chain_key, fresh_evidence.permission_input, now_utc=now
    )
    if presend_permission.decision != _PERMISSION_ALLOW:
        return SubmissionOutcome(
            BLOCKED_PRESEND_RECHECK, order,
            f"pre-send recheck failed ({presend_permission.decision}): {presend_permission.reason}",
        )

    order = transition_order_state(conn, order.id, OrderState.SUBMITTED, detail="order_check passed", now_utc=now)
    append_event(conn, chain_key, "ORDER_SUBMITTED", now, canonical_symbol, {"request": "DEAL"}, strategy_key=None)

    result = gateway.order_send(request)
    interpretation = interpret_retcode(result.retcode)

    if interpretation.is_definitive_rejection:
        order = transition_order_state(
            conn, order.id, OrderState.REJECTED, detail=interpretation.detail,
            broker_order_id=result.broker_order_id, broker_retcode=result.retcode,
            broker_comment=result.comment, raw_broker_response=result.raw, now_utc=now,
        )
        append_event(
            conn, chain_key, "ORDER_REJECTED", now, canonical_symbol,
            {"retcode": result.retcode, "comment": result.comment, "category": interpretation.category.value},
            strategy_key=None, broker_order_id=result.broker_order_id,
        )
        return SubmissionOutcome(REJECTED, order, f"broker rejected the order: {result.comment}")

    if interpretation.order_state == OrderState.UNKNOWN:
        order = transition_order_state(
            conn, order.id, OrderState.UNKNOWN, detail=interpretation.detail,
            broker_order_id=result.broker_order_id, broker_retcode=result.retcode,
            broker_comment=result.comment, raw_broker_response=result.raw, now_utc=now,
        )
        append_event(
            conn, chain_key, "ORDER_UNKNOWN", now, canonical_symbol,
            {"retcode": result.retcode, "detail": interpretation.detail}, strategy_key=None,
            broker_order_id=result.broker_order_id,
        )
        from adaptive_scalper.execution.reconciliation import record_incident
        record_incident(
            conn, "UNKNOWN_OUTCOME",
            f"ambiguous order_send retcode={result.retcode}: {interpretation.detail}",
            order_id=order.id, now_utc=now,
        )
        return SubmissionOutcome(UNKNOWN, order, interpretation.detail)

    if interpretation.order_state == OrderState.CANCELLED:
        order = transition_order_state(
            conn, order.id, OrderState.CANCELLED, detail=interpretation.detail,
            broker_order_id=result.broker_order_id, broker_retcode=result.retcode,
            broker_comment=result.comment, raw_broker_response=result.raw, now_utc=now,
        )
        append_event(
            conn, chain_key, "ORDER_CANCELLED", now, canonical_symbol,
            {"retcode": result.retcode}, strategy_key=None, broker_order_id=result.broker_order_id,
        )
        return SubmissionOutcome(CANCELLED, order, interpretation.detail)

    if interpretation.order_state == OrderState.RESTING:
        order = transition_order_state(
            conn, order.id, OrderState.RESTING, detail=interpretation.detail,
            broker_order_id=result.broker_order_id, broker_retcode=result.retcode,
            broker_comment=result.comment, raw_broker_response=result.raw, now_utc=now,
        )
        append_event(
            conn, chain_key, "ORDER_PENDING", now, canonical_symbol,
            {"retcode": result.retcode}, strategy_key=None, broker_order_id=result.broker_order_id,
        )
        return SubmissionOutcome(RESTING, order, interpretation.detail)

    if interpretation.order_state == OrderState.PARTIAL:
        # execution-safety review finding #2: a PARTIAL fill is REAL
        # broker exposure at the volume actually filled — never treated
        # as a rejection. The exact broker position this partial fill
        # opened is established by the normal reconciliation pass (not
        # inline here — PARTIAL has no legal self-transition to persist
        # a resolved position id without a real state change), which is
        # why any dangerous-UNKNOWN/reconciliation gate upstream of the
        # NEXT entry attempt is what actually protects against
        # unaccounted partial exposure in the meantime.
        order = transition_order_state(
            conn, order.id, OrderState.PARTIAL, detail=interpretation.detail,
            broker_order_id=result.broker_order_id, broker_retcode=result.retcode,
            broker_comment=result.comment, raw_broker_response=result.raw, now_utc=now,
        )
        append_event(
            conn, chain_key, "ORDER_PARTIAL", now, canonical_symbol,
            {"retcode": result.retcode, "volume_filled": result.volume_filled, "price_filled": result.price_filled},
            strategy_key=None, broker_order_id=result.broker_order_id,
        )
        return SubmissionOutcome(PARTIAL, order, interpretation.detail)

    # DONE: broker acknowledgement is still not a fill — go through
    # ACCEPTED, then resolve the true position from broker history.
    order = transition_order_state(
        conn, order.id, OrderState.ACCEPTED, detail="order_send acknowledged (DONE)",
        broker_order_id=result.broker_order_id, broker_retcode=result.retcode,
        broker_comment=result.comment, raw_broker_response=result.raw, now_utc=now,
    )
    append_event(
        conn, chain_key, "ORDER_ACCEPTED", now, canonical_symbol,
        {"retcode": result.retcode}, strategy_key=None, broker_order_id=result.broker_order_id,
        broker_deal_id=result.broker_deal_id,
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
