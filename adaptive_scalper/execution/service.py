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

Flow (directive's execution flow; second-round finding #1, and
2026-09-21 external review findings #12/#13, hardened it further):

    idempotent PROPOSED order
    -> _verify_critical_broker_state() #1: THIS MODULE's own fresh
       account_info/terminal_info (via verify_demo_before_order),
       the REAL persisted kill-switch state, and symbol_info/
       symbol_info_tick -- never solely trusted from the caller (finding
       #12: a fetch_fresh_evidence() callable could return the same
       cached object twice, silently defeating "refresh before send" for
       exactly these fields)
    -> fetch_fresh_evidence() #1 (news/cost/correlation/portfolio/risk/
       re-entry/reconciliation/UNKNOWN/duplicate context — everything
       final permission needs beyond what this module now owns directly)
    -> FRESH evaluate_and_journal_final_permission on that evidence
    -> resolve a broker-supported filling type from THIS MODULE's OWN
       fresh symbol spec (BLOCK_BROKER_CONSTRAINT if none is supported)
    -> exact OrderRequest, built ONCE and never mutated afterward
    -> order_check() against that EXACT request (mandatory, never skipped)
    -> _verify_critical_broker_state() #2 -- a SECOND, independent
       refetch, immediately before send
    -> fetch_fresh_evidence() #2 -- a SECOND, independent call, immediately
       before send
    -> FRESH evaluate_and_journal_final_permission AGAIN on the new
       evidence -- if anything volatile changed (kill switch engaged,
       account no longer DEMO, quote went stale, reconciliation became
       blocking, a duplicate/UNKNOWN appeared, risk/portfolio state
       moved), this call returns something other than ALLOW and
       order_send is NEVER reached
    -> finding #13: a SECOND order_check() of the SAME exact request
       against the freshest broker/symbol state, plus a fresh margin_free
       comparison -- the authoritative final margin/broker recheck
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
from adaptive_scalper.core.kill_switch import get_state as get_kill_switch_state
from adaptive_scalper.core.permission import ActionKind, evaluate_kill_switch_permission
from adaptive_scalper.execution.position_resolution import resolve_opened_position_id
from adaptive_scalper.execution.request_token import embed_request_token
from adaptive_scalper.execution.state_machine import OrderState
from adaptive_scalper.execution.store import OrderRecord, create_local_position, create_order, transition_order_state
from adaptive_scalper.gateway.broker_constraints import derive_filling_type
from adaptive_scalper.gateway.demo_gate import verify_demo_before_order
from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.gateway.retcodes import interpret_retcode
from adaptive_scalper.gateway.symbol_validation import DEFAULT_MAX_EXECUTION_QUOTE_AGE_SECONDS, validate_execution_quote
from adaptive_scalper.gateway.types import OrderAction, OrderRequest, SymbolSpec, SymbolTradeMode
from adaptive_scalper.journal.events import append_event

BLOCK_MARGIN = "BLOCK_MARGIN"
BLOCK_BROKER_CONSTRAINT = "BLOCK_BROKER_CONSTRAINT"

BLOCKED_PERMISSION = "BLOCKED_PERMISSION"
BLOCKED_PRESEND_RECHECK = "BLOCKED_PRESEND_RECHECK"
BLOCKED_BROKER_CONSTRAINT = "BLOCKED_BROKER_CONSTRAINT"
BLOCKED_MARGIN = "BLOCKED_MARGIN"
# External review finding #12: DEMO/connection/trading-permission/symbol-
# trade-state/quote-freshness/kill-switch are never trusted solely from a
# caller-supplied FreshEvidence — a buggy or malicious fetch_fresh_evidence()
# callable could return the same cached object on both calls, silently
# defeating the "refresh immediately before send" guarantee for exactly
# the fields that matter most. This module refetches them itself.
BLOCKED_BROKER_STATE = "BLOCKED_BROKER_STATE"
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


@dataclass(frozen=True)
class _CriticalStateCheck:
    """Result of directly re-fetching the minimum broker-mutating safety
    state this execution boundary owns itself (finding #12) —
    `blocked_detail` is set (and every other field is `None`) on ANY
    failure to positively prove it's safe to proceed; there is no
    default-allow path."""

    blocked_detail: str | None
    symbol_spec: SymbolSpec | None = None
    margin_free: float | None = None


def _verify_critical_broker_state(
    conn, gateway: Gateway, broker_symbol: str, *, max_quote_age_seconds: float, now: float,
) -> _CriticalStateCheck:
    """Independently re-verifies DEMO/connection/trading-permission (via
    `verify_demo_before_order`, a fresh gateway call — never cached), the
    REAL persisted kill-switch state (`core.kill_switch.get_state()`,
    never a caller-supplied `KillSwitchState` snapshot), and symbol trade
    state/quote freshness (`gateway.symbol_info`/`symbol_info_tick`,
    fresh) — the exact five inputs finding #12 names as never solely the
    caller's responsibility. Caller-derived `FreshEvidence` may still
    supply news/cost/correlation/portfolio/risk/RAG context; it is not
    read here at all."""
    demo = verify_demo_before_order(gateway)
    if not demo.allowed:
        return _CriticalStateCheck(f"{demo.block_reason}: {demo.detail}")

    kill_state = get_kill_switch_state(conn)
    kill_check = evaluate_kill_switch_permission(kill_state, ActionKind.NEW_ENTRY)
    if not kill_check.allowed:
        return _CriticalStateCheck(f"{kill_check.block_reason}: kill switch status={kill_state.status.value}")

    symbol_spec = gateway.symbol_info(broker_symbol)
    if symbol_spec is None:
        return _CriticalStateCheck(f"no symbol_info for {broker_symbol!r}")
    if symbol_spec.trade_mode == SymbolTradeMode.DISABLED:
        return _CriticalStateCheck(f"symbol {broker_symbol!r} trade_mode is DISABLED")

    tick = gateway.symbol_info_tick(broker_symbol)
    quote_check = validate_execution_quote(tick, max_quote_age_seconds=max_quote_age_seconds, now=now)
    if not quote_check.valid:
        return _CriticalStateCheck(f"quote check failed for {broker_symbol!r}: {quote_check.reason} — {quote_check.detail}")

    account = gateway.account_info()  # non-None: verify_demo_before_order() above already proved this
    return _CriticalStateCheck(None, symbol_spec=symbol_spec, margin_free=account.margin_free)


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

    # Round 1: this execution boundary's OWN fresh fetch of the minimum
    # broker-mutating safety state (finding #12) -- never solely trusted
    # from the caller's FreshEvidence.
    round1 = _verify_critical_broker_state(
        conn, gateway, broker_symbol, max_quote_age_seconds=DEFAULT_MAX_EXECUTION_QUOTE_AGE_SECONDS, now=float(now),
    )
    if round1.blocked_detail is not None:
        append_event(
            conn, chain_key, "ENTRY_BLOCKED", now, canonical_symbol,
            {"decision": BLOCKED_BROKER_STATE, "reason": round1.blocked_detail}, strategy_key=None,
        )
        return SubmissionOutcome(BLOCKED_BROKER_STATE, order, round1.blocked_detail)

    evidence = fetch_fresh_evidence()
    permission = evaluate_and_journal_final_permission(conn, chain_key, evidence.permission_input, now_utc=now)
    if permission.decision != _PERMISSION_ALLOW:
        return SubmissionOutcome(BLOCKED_PERMISSION, order, permission.reason)

    filling_type = derive_filling_type(round1.symbol_spec.filling_mode)
    if filling_type is None:
        detail = (
            f"no broker-supported filling type for {broker_symbol!r} "
            f"(filling_mode bitmask={round1.symbol_spec.filling_mode})"
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

    # --- finding #1 (round 2) + finding #12: refresh ALL volatile
    # evidence again, independently, immediately before send -- including
    # this boundary's OWN critical-state fetch, never a cached snapshot
    # from either the caller or round 1. ---
    round2 = _verify_critical_broker_state(
        conn, gateway, broker_symbol, max_quote_age_seconds=DEFAULT_MAX_EXECUTION_QUOTE_AGE_SECONDS, now=float(now),
    )
    if round2.blocked_detail is not None:
        return SubmissionOutcome(BLOCKED_BROKER_STATE, order, round2.blocked_detail)

    fresh_evidence = fetch_fresh_evidence()
    presend_permission = evaluate_and_journal_final_permission(
        conn, chain_key, fresh_evidence.permission_input, now_utc=now
    )
    if presend_permission.decision != _PERMISSION_ALLOW:
        return SubmissionOutcome(
            BLOCKED_PRESEND_RECHECK, order,
            f"pre-send recheck failed ({presend_permission.decision}): {presend_permission.reason}",
        )

    # finding #13: final margin/broker recheck immediately before send --
    # a second order_check of the SAME exact (never mutated) request,
    # against the freshest possible broker/symbol state, plus a fresh
    # margin comparison. A filling type that stopped being supported
    # between round 1 and round 2 is a genuine broker-constraint change,
    # not something papered over by silently resending the old value.
    filling_type2 = derive_filling_type(round2.symbol_spec.filling_mode)
    if filling_type2 != filling_type:
        detail = (
            f"broker-supported filling type for {broker_symbol!r} changed between checks "
            f"({filling_type!r} -> {filling_type2!r}) -- refusing to send"
        )
        return SubmissionOutcome(BLOCKED_BROKER_CONSTRAINT, order, detail)

    recheck = gateway.order_check(request)
    if recheck.retcode not in order_check_success_retcodes:
        detail = f"pre-send order_check recheck failed: retcode={recheck.retcode} comment={recheck.comment!r}"
        return SubmissionOutcome(BLOCKED_BROKER_CONSTRAINT, order, detail)

    if (
        round2.margin_free is not None
        and recheck.margin_required is not None
        and recheck.margin_required > round2.margin_free
    ):
        detail = (
            f"pre-send order_check margin_required={recheck.margin_required:.2f} exceeds fresh "
            f"available margin_free={round2.margin_free:.2f}"
        )
        return SubmissionOutcome(BLOCKED_MARGIN, order, detail)

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
        # External review finding #9: DONE_PARTIAL cannot simply return
        # PARTIAL. A partial fill is REAL broker exposure at the volume
        # actually filled — never treated as a rejection, and never left
        # unaccounted in local state until some future reconciliation
        # pass happens to run. Resolve the real broker position (same
        # broker-history lookup the DONE/FILLED path below uses) and, if
        # resolved, immediately persist a local `positions` row scaled to
        # the ACTUAL filled volume — so portfolio/risk code querying
        # local state sees this exposure right now, not after a
        # reconciliation cycle eventually catches up.
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

        partial_resolution = resolve_opened_position_id(
            gateway, broker_deal_id=result.broker_deal_id, broker_order_id=result.broker_order_id,
            window_from_utc=now - history_window_seconds, window_to_utc=now + history_window_seconds,
        )
        if not partial_resolution.resolved:
            # Broker truth for this real partial exposure cannot
            # currently be established -- this is exactly the dangerous
            # case directive section 30 exists for: PENDING_RECONCILIATION/
            # UNKNOWN, and new entries are blocked via the same
            # UNKNOWN_OUTCOME incident every other unresolved case uses,
            # never a guessed local position.
            order = transition_order_state(
                conn, order.id, OrderState.UNKNOWN, detail=partial_resolution.detail, now_utc=now,
            )
            append_event(
                conn, chain_key, "ORDER_UNKNOWN", now, canonical_symbol,
                {"detail": partial_resolution.detail, "context": "partial fill position resolution failed"},
                strategy_key=None, broker_order_id=result.broker_order_id,
            )
            from adaptive_scalper.execution.reconciliation import record_incident
            record_incident(conn, "UNKNOWN_OUTCOME", partial_resolution.detail, order_id=order.id, now_utc=now)
            return SubmissionOutcome(UNKNOWN, order, partial_resolution.detail)

        proposed_monetary_risk = fresh_evidence.permission_input.risk_gate_input.proposed_monetary_risk
        actual_initial_monetary_risk = proposed_monetary_risk * (result.volume_filled / volume) if volume else 0.0
        create_local_position(
            conn, broker_position_id=partial_resolution.broker_position_id, canonical_symbol=canonical_symbol,
            direction=direction, volume=result.volume_filled, entry_price=result.price_filled or 0.0,
            initial_monetary_risk=actual_initial_monetary_risk,
            strategy_key=fresh_evidence.permission_input.signal.strategy_key,
            entry_order_id=order.id, opened_at_utc=now,
        )
        append_event(
            conn, chain_key, "POSITION_OPENED", now, canonical_symbol,
            {
                "volume": result.volume_filled, "price": result.price_filled,
                "initial_monetary_risk": actual_initial_monetary_risk,
                "pending_volume": volume - result.volume_filled, "partial": True,
            },
            strategy_key=None, broker_position_id=partial_resolution.broker_position_id,
        )
        return SubmissionOutcome(
            PARTIAL, order,
            f"{interpretation.detail}; accounted as local position {partial_resolution.broker_position_id} "
            f"(volume={result.volume_filled}, pending={volume - result.volume_filled})",
        )

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
    create_local_position(
        conn, broker_position_id=resolution.broker_position_id, canonical_symbol=canonical_symbol,
        direction=direction, volume=result.volume_filled, entry_price=result.price_filled or 0.0,
        initial_monetary_risk=fresh_evidence.permission_input.risk_gate_input.proposed_monetary_risk,
        strategy_key=fresh_evidence.permission_input.signal.strategy_key,
        entry_order_id=order.id, opened_at_utc=now,
    )
    append_event(
        conn, chain_key, "POSITION_OPENED", now, canonical_symbol,
        {"volume": result.volume_filled, "price": result.price_filled}, strategy_key=None,
        broker_position_id=resolution.broker_position_id,
    )
    return SubmissionOutcome(FILLED, order, f"filled and resolved to position {resolution.broker_position_id}")
