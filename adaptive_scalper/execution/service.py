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
from adaptive_scalper.execution.entry_fills import RECOMPUTED, record_entry_fills
from adaptive_scalper.execution.position_resolution import resolve_entry_fill_evidence
from adaptive_scalper.execution.request_token import embed_request_token
from adaptive_scalper.execution.state_machine import OrderState
from adaptive_scalper.execution.store import (
    OrderRecord,
    create_order,
    record_order_risk_accounting,
    transition_order_state,
)
from adaptive_scalper.gateway.broker_constraints import derive_filling_type
from adaptive_scalper.gateway.demo_gate import verify_demo_before_order
from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.gateway.retcodes import interpret_retcode
from adaptive_scalper.gateway.symbol_validation import (
    DEFAULT_MAX_EXECUTION_QUOTE_AGE_SECONDS,
    identity_matches_canonical,
    validate_direction_for_new_exposure,
    validate_execution_quote,
)
from adaptive_scalper.gateway.types import OrderAction, OrderRequest, SymbolSpec
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
    conn, gateway: Gateway, canonical_symbol: str, broker_symbol: str, direction: str, *,
    max_quote_age_seconds: float, clock: Callable[[], float],
) -> _CriticalStateCheck:
    """Independently re-verifies DEMO/connection/trading-permission (via
    `verify_demo_before_order`, a fresh gateway call — never cached), the
    REAL persisted kill-switch state (`core.kill_switch.get_state()`,
    never a caller-supplied `KillSwitchState` snapshot), canonical asset
    identity against FRESH broker metadata, directional trade-mode
    permission for the EXACT requested direction, and quote freshness —
    the inputs finding #12 (and finding #2's follow-up) name as never
    solely the caller's responsibility. Caller-derived `FreshEvidence`
    may still supply news/cost/correlation/portfolio/risk/RAG context; it
    is not read here at all.

    `clock`: called ONCE, freshly, right here — never a `now` value
    computed earlier in this submission's lifecycle and carried forward
    (external review finding #1, 2026-09-21: a frozen wall-clock value
    reused across both rounds means a quote can go genuinely stale in
    real time without ever appearing stale to the round-2 check). This is
    deliberately a SEPARATE clock from the event/journal timestamp
    (`now_utc`), which legitimately stays fixed for one submission's
    journal entries.
    """
    demo = verify_demo_before_order(gateway)
    if not demo.allowed:
        return _CriticalStateCheck(f"{demo.block_reason}: {demo.detail}")

    kill_state = get_kill_switch_state(conn)
    kill_check = evaluate_kill_switch_permission(kill_state, ActionKind.NEW_ENTRY)
    if not kill_check.allowed:
        return _CriticalStateCheck(f"{kill_check.block_reason}: kill switch status={kill_state.status.value}")

    now = clock()

    symbol_spec = gateway.symbol_info(broker_symbol)
    if symbol_spec is None:
        return _CriticalStateCheck(f"no symbol_info for {broker_symbol!r}")

    # Finding #2: a name match / trade_mode!=DISABLED alone is not
    # enough. Re-verify canonical asset identity against FRESH broker
    # metadata (never trusted solely from the caller's stale
    # FinalPermissionInput.asset_identity) -- and FRESH directional
    # trade-mode permission for the EXACT requested direction: CLOSEONLY
    # blocks any new entry, LONGONLY/SHORTONLY restrict which direction
    # is allowed, never trusted solely from the caller's
    # FinalPermissionInput.direction_check.
    if not identity_matches_canonical(symbol_spec, canonical_symbol):
        return _CriticalStateCheck(
            f"asset_identity_mismatch: fresh broker metadata for {broker_symbol!r} no longer matches "
            f"canonical {canonical_symbol!r} (currency_base={symbol_spec.currency_base!r} "
            f"currency_profit={symbol_spec.currency_profit!r} description={symbol_spec.description!r})"
        )

    direction_check = validate_direction_for_new_exposure(symbol_spec.trade_mode, direction)
    if not direction_check.valid:
        return _CriticalStateCheck(f"{direction_check.reason}: {direction_check.detail}")

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
    clock: Callable[[], float] = time.time,
) -> SubmissionOutcome:
    """`clock`: the FRESHNESS clock (external review finding #1) —
    called independently at each critical-state round, never frozen. This
    is deliberately separate from `now_utc`, the EVENT/JOURNAL timestamp,
    which legitimately stays fixed for every journal entry this one
    submission produces. A test proving real staleness detection passes
    a fake `clock` that advances between round 1 and round 2; production
    callers should simply omit it (defaults to `time.time`)."""
    now = now_utc if now_utc is not None else int(time.time())

    order = create_order(
        conn, client_request_id, canonical_symbol, broker_symbol, direction, volume,
        chain_key=chain_key, stop_loss=stop_loss, take_profit=take_profit, magic=magic, now_utc=now,
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
        conn, gateway, canonical_symbol, broker_symbol, direction,
        max_quote_age_seconds=DEFAULT_MAX_EXECUTION_QUOTE_AGE_SECONDS, clock=clock,
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

    # External review findings #5/#6: as soon as the intended risk is
    # known, persist it as a durable, typed field on the order row --
    # reconstructable from SQLite alone after a restart, not only from an
    # in-memory proposal. The WHOLE requested volume/risk starts out
    # entirely "remaining/pending"; FILLED/PARTIAL below narrow it as
    # real fills are confirmed.
    requested_monetary_risk = evidence.permission_input.risk_gate_input.proposed_monetary_risk
    record_order_risk_accounting(
        conn, order.id, requested_monetary_risk=requested_monetary_risk,
        remaining_volume=volume, remaining_pending_monetary_risk=requested_monetary_risk, now_utc=now,
    )

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
        conn, gateway, canonical_symbol, broker_symbol, direction,
        max_quote_age_seconds=DEFAULT_MAX_EXECUTION_QUOTE_AGE_SECONDS, clock=clock,
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

        # External review finding #8: never entry_price=0.0. Resolve REAL
        # entry-deal evidence (position id + a positive weighted-average
        # fill price) — never OrderSendResult.price_filled-or-0.0.
        fill_evidence = resolve_entry_fill_evidence(
            gateway, broker_deal_id=result.broker_deal_id, broker_order_id=result.broker_order_id,
            window_from_utc=now - history_window_seconds, window_to_utc=now + history_window_seconds,
        )
        if not fill_evidence.resolved:
            # Broker truth for this real partial exposure cannot
            # currently be established -- this is exactly the dangerous
            # case directive section 30 exists for: PENDING_RECONCILIATION/
            # UNKNOWN, and new entries are blocked via the same
            # UNKNOWN_OUTCOME incident every other unresolved case uses,
            # never a guessed local position.
            order = transition_order_state(
                conn, order.id, OrderState.UNKNOWN, detail=fill_evidence.detail, now_utc=now,
            )
            append_event(
                conn, chain_key, "ORDER_UNKNOWN", now, canonical_symbol,
                {"detail": fill_evidence.detail, "context": "partial fill entry evidence unresolved"},
                strategy_key=None, broker_order_id=result.broker_order_id,
            )
            from adaptive_scalper.execution.reconciliation import record_incident
            record_incident(conn, "UNKNOWN_OUTCOME", fill_evidence.detail, order_id=order.id, now_utc=now)
            return SubmissionOutcome(UNKNOWN, order, fill_evidence.detail)

        # External review findings #7/#9: persist EVERY entry deal (never
        # just the position row) and recompute the position's aggregate
        # from ALL persisted entry deals — safe to call again if MORE
        # fills trickle in for the same broker_position_id later.
        fill_record = record_entry_fills(
            conn, order_id=order.id, canonical_symbol=canonical_symbol, direction=direction,
            strategy_key=fresh_evidence.permission_input.signal.strategy_key, evidence=fill_evidence,
            requested_volume=volume, requested_monetary_risk=requested_monetary_risk, now_utc=now,
        )
        record_order_risk_accounting(
            conn, order.id, filled_volume=fill_evidence.total_filled_volume,
            filled_initial_monetary_risk=(fill_record.initial_monetary_risk or 0.0),
            remaining_volume=max(0.0, volume - fill_evidence.total_filled_volume),
            remaining_pending_monetary_risk=max(0.0, requested_monetary_risk - (fill_record.initial_monetary_risk or 0.0)),
            now_utc=now,
        )
        append_event(
            conn, chain_key, "POSITION_OPENED", now, canonical_symbol,
            {
                "volume": fill_record.volume, "price": fill_record.entry_price,
                "initial_monetary_risk": fill_record.initial_monetary_risk,
                "pending_volume": volume - fill_evidence.total_filled_volume, "partial": True,
                "fill_record_status": fill_record.status,
            },
            strategy_key=None, broker_position_id=fill_evidence.broker_position_id,
        )
        return SubmissionOutcome(
            PARTIAL, order,
            f"{interpretation.detail}; accounted as local position {fill_evidence.broker_position_id} "
            f"(volume={fill_record.volume}, pending={volume - fill_evidence.total_filled_volume})",
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

    # External review finding #8: never entry_price=0.0 -- resolve REAL
    # entry-deal evidence (position id + a positive weighted-average fill
    # price), never OrderSendResult.price_filled-or-0.0.
    fill_evidence = resolve_entry_fill_evidence(
        gateway, broker_deal_id=result.broker_deal_id, broker_order_id=result.broker_order_id,
        window_from_utc=now - history_window_seconds, window_to_utc=now + history_window_seconds,
    )
    if not fill_evidence.resolved:
        order = transition_order_state(
            conn, order.id, OrderState.UNKNOWN, detail=fill_evidence.detail, now_utc=now,
        )
        append_event(
            conn, chain_key, "ORDER_UNKNOWN", now, canonical_symbol,
            {"detail": fill_evidence.detail}, strategy_key=None, broker_order_id=result.broker_order_id,
        )
        from adaptive_scalper.execution.reconciliation import record_incident
        record_incident(conn, "UNKNOWN_OUTCOME", fill_evidence.detail, order_id=order.id, now_utc=now)
        return SubmissionOutcome(UNKNOWN, order, fill_evidence.detail)

    order = transition_order_state(
        conn, order.id, OrderState.FILLED, detail="position resolved from broker history",
        broker_position_id=fill_evidence.broker_position_id, now_utc=now,
    )
    append_event(
        conn, chain_key, "ORDER_FILLED", now, canonical_symbol,
        {"volume_filled": result.volume_filled, "price_filled": result.price_filled}, strategy_key=None,
        broker_order_id=result.broker_order_id, broker_position_id=fill_evidence.broker_position_id,
        broker_deal_id=result.broker_deal_id,
    )
    # External review findings #7/#9: persist EVERY entry deal and
    # recompute the position's aggregate from ALL persisted entry deals.
    fill_record = record_entry_fills(
        conn, order_id=order.id, canonical_symbol=canonical_symbol, direction=direction,
        strategy_key=fresh_evidence.permission_input.signal.strategy_key, evidence=fill_evidence,
        requested_volume=volume, requested_monetary_risk=requested_monetary_risk, now_utc=now,
    )
    record_order_risk_accounting(
        conn, order.id, filled_volume=fill_evidence.total_filled_volume,
        filled_initial_monetary_risk=(fill_record.initial_monetary_risk or 0.0),
        remaining_volume=max(0.0, volume - fill_evidence.total_filled_volume),
        remaining_pending_monetary_risk=max(0.0, requested_monetary_risk - (fill_record.initial_monetary_risk or 0.0)),
        now_utc=now,
    )
    append_event(
        conn, chain_key, "POSITION_OPENED", now, canonical_symbol,
        {"volume": fill_record.volume, "price": fill_record.entry_price, "fill_record_status": fill_record.status},
        strategy_key=None, broker_position_id=fill_evidence.broker_position_id,
    )
    return SubmissionOutcome(FILLED, order, f"filled and resolved to position {fill_evidence.broker_position_id}")
