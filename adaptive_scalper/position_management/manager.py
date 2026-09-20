"""Position manager review loop (directive sections 15, 17-23).

Ties together continuous position expectancy (`expectancy.py`), the
adaptive exit decision core (`adaptive_exit.py`), durable state
(`state_store.py`), and the two safe broker-mutating services
(`execution.close.close_position_safely`,
`execution.stop_modification.modify_protective_stop_safely`) into ONE
per-position review cycle: `review_position_once()`.

Existing open exposure gets priority over new-entry scanning (directive)
— this module is the position-management side of that priority; the
entry-scanning loop itself is a separate, still-pending runtime-wiring
concern (see PROJECT_STATUS.md). Directive's approximate cadences (0.5-1s
position review, ~4s entry scan) are the CALLER's responsibility — this
function is stateless between calls beyond what it persists via
`state_store`, so calling it more or less often never corrupts anything,
it only changes review latency.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from adaptive_scalper.execution.close import CloseOutcome, close_position_safely
from adaptive_scalper.execution.stop_modification import StopModificationOutcome, modify_protective_stop_safely
from adaptive_scalper.gateway.protocol import Gateway
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


@dataclass(frozen=True)
class PositionReviewResult:
    action: str  # HOLD / MOVE_PROTECTIVE_STOP / FULL_CLOSE
    current_r: float | None
    peak_r: float
    reasons: tuple[str, ...]
    stop_outcome: StopModificationOutcome | None = None
    close_outcome: CloseOutcome | None = None


def _price_from_r(entry_price: float, initial_stop_distance_price: float, r: float, direction: str) -> float:
    if direction == "BUY":
        return entry_price + r * initial_stop_distance_price
    if direction == "SELL":
        return entry_price - r * initial_stop_distance_price
    raise ValueError(f"direction must be 'BUY' or 'SELL', got {direction!r}")


def review_position_once(
    conn,
    gateway: Gateway,
    inp: PositionReviewInput,
    *,
    params: AdaptiveExitParams = AdaptiveExitParams(),
    now_utc: int | None = None,
) -> PositionReviewResult:
    now = now_utc if now_utc is not None else int(time.time())

    state_store.get_or_create_state(
        conn, inp.position_id, initial_monetary_risk=inp.initial_monetary_risk,
        entry_regime=inp.entry_regime, now_utc=now,
    )

    current_r = compute_current_r(inp.initial_monetary_risk, inp.unrealized_pnl)
    if current_r is None:
        state = state_store.get_state(conn, inp.position_id)
        return PositionReviewResult(
            HOLD, None, state.peak_r,
            ("initial_monetary_risk is non-positive/invalid — R quarantined, holding without action",),
        )

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

    if decision.action == HOLD:
        return PositionReviewResult(HOLD, current_r, state.peak_r, reasons)

    if decision.action == MOVE_PROTECTIVE_STOP:
        proposed_stop_price = _price_from_r(
            inp.entry_price, inp.initial_stop_distance_price, decision.new_stop_r, inp.direction,
        )
        stop_outcome = modify_protective_stop_safely(
            gateway, broker_position_id=inp.broker_position_id, expected_direction=inp.direction,
            expected_volume=inp.volume, broker_symbol=inp.broker_symbol, proposed_stop_price=proposed_stop_price,
        )
        return PositionReviewResult(MOVE_PROTECTIVE_STOP, current_r, state.peak_r, reasons, stop_outcome=stop_outcome)

    # FULL_CLOSE
    state_store.record_exit_decision(conn, inp.position_id, decision_r=current_r, decision_at_utc=now, now_utc=now)
    close_outcome = close_position_safely(
        gateway, broker_position_id=inp.broker_position_id, expected_direction=inp.direction,
        expected_volume=inp.volume, broker_symbol=inp.broker_symbol,
        conn=conn, reconciliation_chain_key=f"position-manager:{inp.position_id}",
    )
    state_store.record_exit_request(conn, inp.position_id, request_at_utc=now, now_utc=now)
    # The AUTHORITATIVE fill price/R (and therefore giveback_fill) is not
    # known synchronously here — it comes from the closing deal
    # reconciliation just triggered establishes. record_exit_fill() is
    # deliberately NOT called from this function; a reconciliation-driven
    # follow-up owns writing the real fill once broker truth confirms it
    # (named gap: that follow-up wiring doesn't exist yet — see
    # BUG_BACKLOG.md).
    return PositionReviewResult(FULL_CLOSE, current_r, state.peak_r, reasons, close_outcome=close_outcome)
