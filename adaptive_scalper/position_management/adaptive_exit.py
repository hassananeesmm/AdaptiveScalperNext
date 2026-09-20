"""Adaptive exit decision logic (directive sections 17-23).

Broker SL/TP remain mandatory hard protection — this module NEVER
replaces them. It only decides whether the CURRENT position's remaining
expected value still justifies holding and, if not, whether to move the
protective stop or fully close. Initial monetary risk is fixed at entry
and NEVER changes: `current_r`/`peak_r` are always computed against that
original risk, never re-based off a moved stop (directive section 21 —
"Initial monetary risk remains fixed... Moving SL never redefines R").

Partial close is disabled in this initial build (directive section 20:
`partial_close_enabled = false`) — the only actions are `HOLD`,
`MOVE_PROTECTIVE_STOP`, and `FULL_CLOSE`.
"""

from __future__ import annotations

from dataclasses import dataclass

HOLD = "HOLD"
MOVE_PROTECTIVE_STOP = "MOVE_PROTECTIVE_STOP"
FULL_CLOSE = "FULL_CLOSE"


@dataclass(frozen=True)
class AdaptiveExitParams:
    """Directive section 20's exact initial research defaults — starting
    points for validation, not claimed optimal or profitable."""

    min_profit_r: float = 0.30
    profit_protection_trigger_r: float = 0.60
    max_profit_retracement_r: float = 0.20
    early_take_profit_r: float = 1.00
    breakeven_enabled: bool = True
    breakeven_trigger_r: float = 0.40
    breakeven_floor_r: float = 0.05
    setup_deterioration_exit: bool = True
    regime_reversal_exit: bool = True
    max_holding_enabled: bool = True
    max_holding_seconds: int = 600


@dataclass(frozen=True)
class ExitDecision:
    action: str
    reason: str
    new_stop_r: float | None = None   # for MOVE_PROTECTIVE_STOP: the new stop, in R terms


def compute_current_r(initial_monetary_risk: float, unrealized_pnl: float) -> float | None:
    """`None` (never a divide-by-near-zero) if `initial_monetary_risk`
    is non-positive — directive section 59: an unprovable/corrupted R is
    QUARANTINED, not silently computed anyway."""
    if initial_monetary_risk <= 0:
        return None
    return unrealized_pnl / initial_monetary_risk


def evaluate_adaptive_exit(
    *,
    current_r: float,
    peak_r: float,
    holding_seconds: int,
    thesis_valid: bool,
    regime_reversed: bool,
    params: AdaptiveExitParams = AdaptiveExitParams(),
) -> ExitDecision:
    """`thesis_valid`/`regime_reversed` are pre-computed by the caller
    (continuous position expectancy re-evaluation, directive section 17)
    — this function is the deterministic decision core, not the
    evidence-gathering step, matching every other gate built this
    session (pure function, caller does the orchestration/I-O).

    Checked in a fixed priority order: max holding time and thesis/regime
    invalidation are checked FIRST (they apply regardless of current
    profitability — a position that no longer makes sense should close
    even while modestly profitable), then early-profit-target, then
    profit-giveback protection, then breakeven advancement, then HOLD.
    """
    if params.max_holding_enabled and holding_seconds >= params.max_holding_seconds:
        return ExitDecision(FULL_CLOSE, f"max holding time reached ({holding_seconds}s >= {params.max_holding_seconds}s)")

    if params.setup_deterioration_exit and not thesis_valid:
        return ExitDecision(FULL_CLOSE, "original strategy thesis invalidated")

    if params.regime_reversal_exit and regime_reversed:
        return ExitDecision(FULL_CLOSE, "regime materially reversed against the position")

    if current_r >= params.early_take_profit_r:
        return ExitDecision(FULL_CLOSE, f"early profit objective reached (current_r={current_r:.2f} >= {params.early_take_profit_r})")

    giveback = peak_r - current_r
    # Tiny epsilon guards float round-trip error at exact threshold
    # boundaries (e.g. 0.85 - 0.65 == 0.19999999999999996 in binary
    # floating point, not the mathematically exact 0.20) — the same
    # pattern used in risk/governor.py's volume-step rounding.
    if peak_r >= params.profit_protection_trigger_r and giveback >= params.max_profit_retracement_r - 1e-9:
        return ExitDecision(
            FULL_CLOSE,
            f"profit giveback {giveback:.2f}R from peak {peak_r:.2f}R exceeds max retracement "
            f"{params.max_profit_retracement_r}R",
        )

    if params.breakeven_enabled and current_r >= params.breakeven_trigger_r:
        return ExitDecision(
            MOVE_PROTECTIVE_STOP,
            f"breakeven trigger reached (current_r={current_r:.2f} >= {params.breakeven_trigger_r})",
            new_stop_r=params.breakeven_floor_r,
        )

    return ExitDecision(HOLD, "no exit condition met")


def resolve_new_stop_price(current_stop_price: float, proposed_stop_price: float, direction: str) -> float:
    """Never moves a protective stop backward (directive section 23).
    "Forward" for a BUY means higher (toward locking in more profit);
    for a SELL it means lower."""
    if direction == "BUY":
        return max(current_stop_price, proposed_stop_price)
    if direction == "SELL":
        return min(current_stop_price, proposed_stop_price)
    raise ValueError(f"direction must be 'BUY' or 'SELL', got {direction!r}")
