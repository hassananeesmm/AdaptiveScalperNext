"""Position manager review loop (directive sections 15, 17-23, 54).

Ties together continuous position expectancy (`expectancy.py`), the
adaptive exit decision core (`adaptive_exit.py`), durable state
(`state_store.py`), the two safe broker-mutating services
(`execution.close.close_position_safely`,
`execution.stop_modification.modify_protective_stop_safely`), and the
immutable decision journal into ONE per-position review cycle:
`review_position_once()`.

Existing open exposure gets priority over new-entry scanning (directive)
— this module is the position-management side of that priority; the
entry-scanning loop itself is a separate, still-pending runtime-wiring
concern (see PROJECT_STATUS.md). Directive's approximate cadences (0.5-1s
position review, ~4s entry scan) are the CALLER's responsibility — this
function is stateless between calls beyond what it persists via
`state_store`, so calling it more or less often never corrupts anything,
it only changes review latency.

**Invalid initial risk is a degraded-health incident, never an ordinary
HOLD** (external review finding #6): if `initial_monetary_risk` cannot be
positively proven positive/finite, this function never calls
`state_store.get_or_create_state()` with the bad value (which would now
raise — see `state_store.PositionStateConflictError`'s sibling
validation) and never invents an R. It records a
`position_risk_incidents` row and returns a `HOLD` result with
`quarantined=True`, distinguishable from a healthy HOLD by both that flag
and the incident record — broker SL/TP protection is left untouched
either way, since HOLD never touches it.

**Exit-request timestamps are only recorded for outcomes that actually
reached the broker** (external review finding #4): `close.POST_SEND_STATUSES`
(`SENT`/`REJECTED`/`UNKNOWN`) is the sole gate for calling
`state_store.record_exit_request()` — a pre-send block (`NOT_DEMO`,
`ALREADY_CLOSED`, `VOLUME_MISMATCH`, `BROKER_CONSTRAINT`) never fabricates
a request timestamp for a request that was never transmitted.

**Real broker fill truth, not a guess, completes the exit timeline**
(external review finding #8 / BUG_BACKLOG.md item 8): when
`close_position_safely()`'s triggered reconciliation pass actually
recovers this position from a real broker closing deal,
`state_store.record_exit_fill()` is called with the REAL deal's
price/profit/commission/swap-derived R — never the current quote standing
in for a historical fill. If reconciliation could not (yet) confirm the
real fill, `fill_r`/`giveback_fill` correctly stay unset rather than being
fabricated; a later reconciliation-driven pass (still a named gap for a
position closed via any other path than this function) would need to
complete it then.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from adaptive_scalper.execution.close import POST_SEND_STATUSES, SENT as CLOSE_SENT, CloseOutcome, close_position_safely
from adaptive_scalper.execution.stop_modification import SENT as STOP_SENT, StopModificationOutcome, modify_protective_stop_safely
from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.journal.events import append_event
from adaptive_scalper.position_management import state_store
from adaptive_scalper.position_management.adaptive_exit import (
    FULL_CLOSE,
    HOLD,
    MOVE_PROTECTIVE_STOP,
    AdaptiveExitParams,
    compute_current_r,
    evaluate_adaptive_exit,
)
from adaptive_scalper.position_management.expectancy import ExpectancyEvidence, evaluate_position_expectancy


@dataclass(frozen=True)
class PositionReviewInput:
    position_id: int
    broker_position_id: str
    canonical_symbol: str
    broker_symbol: str
    direction: str
    volume: float
    entry_price: float
    # The ORIGINAL stop distance (in price units) that defined initial
    # risk at entry — used ONLY to translate an adaptive-exit new_stop_r
    # back into a real price; never re-derived from a moved stop
    # (directive section 21).
    initial_stop_distance_price: float
    initial_monetary_risk: float
    unrealized_pnl: float
    entry_regime: str
    current_regime: str
    strategy_setup_still_valid: bool
    current_net_edge_price: float | None
    min_required_edge_price: float
    holding_seconds: int
    rag_advisory_negative: bool = False
    model_advisory_negative: bool = False
    strategy_key: str | None = None


@dataclass(frozen=True)
class PositionReviewResult:
    action: str  # HOLD / MOVE_PROTECTIVE_STOP / FULL_CLOSE
    current_r: float | None
    peak_r: float
    reasons: tuple[str, ...]
    stop_outcome: StopModificationOutcome | None = None
    close_outcome: CloseOutcome | None = None
    quarantined: bool = False


def _price_from_r(entry_price: float, initial_stop_distance_price: float, r: float, direction: str) -> float:
    if direction == "BUY":
        return entry_price + r * initial_stop_distance_price
    if direction == "SELL":
        return entry_price - r * initial_stop_distance_price
    raise ValueError(f"direction must be 'BUY' or 'SELL', got {direction!r}")


def _review_chain_key(position_id: int) -> str:
    return f"position-review:{position_id}"


def _reconciliation_chain_key(position_id: int) -> str:
    return f"position-manager:{position_id}"


def _journal_position_reviewed(
    conn, inp: PositionReviewInput, now: int, *,
    current_r: float | None, peak_r: float, action: str, reasons: tuple[str, ...], quarantined: bool,
) -> None:
    append_event(
        conn, _review_chain_key(inp.position_id), "POSITION_REVIEWED", now, inp.canonical_symbol,
        {
            "position_id": inp.position_id,
            "broker_position_id": inp.broker_position_id,
            "current_r": current_r,
            "peak_r": peak_r,
            "entry_regime": inp.entry_regime,
            "current_regime": inp.current_regime,
            "current_net_edge_price": inp.current_net_edge_price,
            "holding_seconds": inp.holding_seconds,
            "selected_action": action,
            "reason": "; ".join(reasons),
            "quarantined": quarantined,
        },
        broker_symbol=inp.broker_symbol, strategy_key=inp.strategy_key, broker_position_id=inp.broker_position_id,
    )


def _journal_stop_advanced(
    conn, inp: PositionReviewInput, now: int, stop_outcome: StopModificationOutcome, *, current_r: float, peak_r: float,
) -> None:
    append_event(
        conn, _review_chain_key(inp.position_id), "STOP_ADVANCED", now, inp.canonical_symbol,
        {
            "position_id": inp.position_id,
            "broker_position_id": inp.broker_position_id,
            "new_stop_price": stop_outcome.new_stop_price,
            "current_r": current_r,
            "peak_r": peak_r,
            "reason": stop_outcome.detail,
        },
        broker_symbol=inp.broker_symbol, strategy_key=inp.strategy_key, broker_position_id=inp.broker_position_id,
    )


def _maybe_record_exit_fill(conn, inp: PositionReviewInput, close_outcome: CloseOutcome, now: int) -> None:
    """Only writes real broker-confirmed fill truth — never a guess.
    `close_outcome.reconciliation` is populated exactly when
    `close_position_safely()` was given a `conn`/`reconciliation_chain_key`
    (always true from this module) and ran; this position's
    `broker_position_id` appears in `recovered_position_ids` only when the
    reconciliation pass found and applied the REAL authoritative closing
    deal from broker history (`execution/reconciliation.py`)."""
    report = close_outcome.reconciliation
    if report is None or inp.broker_position_id not in report.recovered_position_ids:
        return

    deal = conn.execute(
        "SELECT * FROM deals WHERE broker_position_id = ? ORDER BY occurred_at_utc DESC LIMIT 1",
        (inp.broker_position_id,),
    ).fetchone()
    if deal is None:
        return

    realized_net = deal["profit"] + deal["commission"] + deal["swap"]
    fill_r = realized_net / inp.initial_monetary_risk
    state_store.record_exit_fill(
        conn, inp.position_id, fill_r=fill_r, broker_response_at_utc=deal["occurred_at_utc"], now_utc=now,
    )


def review_position_once(
    conn,
    gateway: Gateway,
    inp: PositionReviewInput,
    *,
    params: AdaptiveExitParams = AdaptiveExitParams(),
    now_utc: int | None = None,
) -> PositionReviewResult:
    now = now_utc if now_utc is not None else int(time.time())

    if not math.isfinite(inp.initial_monetary_risk) or inp.initial_monetary_risk <= 0:
        # Quarantine, not a healthy HOLD (external review finding #6):
        # never invent an R, never create position_management_state with
        # an invalid value, record a degraded-health incident instead.
        state_store.record_risk_incident(
            conn, inp.position_id,
            f"initial_monetary_risk={inp.initial_monetary_risk!r} is non-positive/non-finite — "
            f"R-based position management quarantined for position_id={inp.position_id}; "
            f"broker SL/TP protection is left untouched",
            now_utc=now,
        )
        existing = state_store.get_state(conn, inp.position_id)
        peak_r = existing.peak_r if existing is not None else 0.0
        reasons = (
            "initial_monetary_risk is non-positive/invalid — R quarantined, degraded-health incident "
            "recorded, holding without R-based action",
        )
        _journal_position_reviewed(
            conn, inp, now, current_r=None, peak_r=peak_r, action=HOLD, reasons=reasons, quarantined=True,
        )
        return PositionReviewResult(HOLD, None, peak_r, reasons, quarantined=True)

    state_store.get_or_create_state(
        conn, inp.position_id, initial_monetary_risk=inp.initial_monetary_risk,
        entry_regime=inp.entry_regime, now_utc=now,
    )

    current_r = compute_current_r(inp.initial_monetary_risk, inp.unrealized_pnl)
    if current_r is None:
        # Defensive: validated positive/finite above, so this should be
        # unreachable via compute_current_r's own non-positive check —
        # kept as a second independent guard, never silently proceeding
        # with a fabricated R if it somehow triggers anyway.
        state = state_store.get_state(conn, inp.position_id)
        reasons = ("current_r could not be computed despite valid initial risk — quarantined defensively, holding",)
        _journal_position_reviewed(
            conn, inp, now, current_r=None, peak_r=state.peak_r, action=HOLD, reasons=reasons, quarantined=True,
        )
        return PositionReviewResult(HOLD, None, state.peak_r, reasons, quarantined=True)

    state = state_store.record_review(conn, inp.position_id, current_r=current_r, latest_regime=inp.current_regime, now_utc=now)

    expectancy = evaluate_position_expectancy(ExpectancyEvidence(
        entry_regime=inp.entry_regime, current_regime=inp.current_regime,
        strategy_setup_still_valid=inp.strategy_setup_still_valid,
        current_net_edge_price=inp.current_net_edge_price, min_required_edge_price=inp.min_required_edge_price,
        holding_seconds=inp.holding_seconds, current_r=current_r, peak_r=state.peak_r,
        rag_advisory_negative=inp.rag_advisory_negative, model_advisory_negative=inp.model_advisory_negative,
    ))

    decision = evaluate_adaptive_exit(
        current_r=current_r, peak_r=state.peak_r, holding_seconds=inp.holding_seconds,
        thesis_valid=expectancy.thesis_valid, regime_reversed=expectancy.regime_reversed, params=params,
    )
    reasons = expectancy.reasons + (decision.reason,)

    _journal_position_reviewed(
        conn, inp, now, current_r=current_r, peak_r=state.peak_r, action=decision.action, reasons=reasons,
        quarantined=False,
    )

    if decision.action == HOLD:
        return PositionReviewResult(HOLD, current_r, state.peak_r, reasons)

    if decision.action == MOVE_PROTECTIVE_STOP:
        proposed_stop_price = _price_from_r(
            inp.entry_price, inp.initial_stop_distance_price, decision.new_stop_r, inp.direction,
        )
        stop_outcome = modify_protective_stop_safely(
            gateway, broker_position_id=inp.broker_position_id, expected_direction=inp.direction,
            expected_volume=inp.volume, broker_symbol=inp.broker_symbol, proposed_stop_price=proposed_stop_price,
            now=float(now),
        )
        if stop_outcome.status == STOP_SENT:
            _journal_stop_advanced(conn, inp, now, stop_outcome, current_r=current_r, peak_r=state.peak_r)
        return PositionReviewResult(MOVE_PROTECTIVE_STOP, current_r, state.peak_r, reasons, stop_outcome=stop_outcome)

    # FULL_CLOSE
    state_store.record_exit_decision(conn, inp.position_id, decision_r=current_r, decision_at_utc=now, now_utc=now)
    close_outcome = close_position_safely(
        gateway, broker_position_id=inp.broker_position_id, expected_direction=inp.direction,
        expected_volume=inp.volume, broker_symbol=inp.broker_symbol,
        conn=conn, reconciliation_chain_key=_reconciliation_chain_key(inp.position_id),
    )
    if close_outcome.status in POST_SEND_STATUSES:
        # A request actually reached the broker (filled, rejected, or
        # uncertain) — only then is request_at_utc real (finding #4).
        state_store.record_exit_request(conn, inp.position_id, request_at_utc=now, now_utc=now)

    if close_outcome.status == CLOSE_SENT:
        _maybe_record_exit_fill(conn, inp, close_outcome, now)

    return PositionReviewResult(FULL_CLOSE, current_r, state.peak_r, reasons, close_outcome=close_outcome)
