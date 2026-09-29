"""H1 -- the ENTRY trigger and the HOLDING thesis are separate questions.

V1 re-runs the strategy's entry trigger on every bar of an open position
and closes it ("original strategy thesis invalidated") as soon as the
trigger no longer fires in the same direction. An entry trigger (a burst,
an excursion) is by design a transient condition, so that test can close
a sound position merely because the burst ended.

A holding thesis here only answers "is the position still justified?".
It is consulted by `backtest.engine._review_open_trade` in place of the
V1 re-trigger test and feeds the SAME adaptive-exit rules. The other half
of V1's thesis test -- remaining edge to target, net of the current cost,
must stay above the minimum ("expected remaining reward") -- is KEPT in
every variant (`position_management.expectancy`). It cannot move
the stop (the engine only ever moves it forward), resize, add risk or
average down.

Each thesis is called as `thesis(direction=, entry_regime=,
confirmed_regime=, fresh_signal=)` where `fresh_signal` is the original
strategy's current signal in ANY direction, or None.
"""

from __future__ import annotations

from adaptive_scalper.regimes.classifier import TRENDING_DOWN, TRENDING_UP

_OPPOSING_TREND = {"BUY": TRENDING_DOWN, "SELL": TRENDING_UP}


def v1_entry_retrigger(*, direction, entry_regime, confirmed_regime, fresh_signal) -> bool:
    """Reference: exactly V1's rule (used only to prove hook equivalence)."""
    return fresh_signal is not None and fresh_signal.direction == direction


def directional_invalidation(*, direction, entry_regime, confirmed_regime, fresh_signal) -> bool:
    """H1-DIR: valid unless the evidence now points the OTHER way -- the
    original strategy fires in the opposite direction, or the confirmed
    regime is a trend against the position. Silence is not invalidation."""
    if fresh_signal is not None and fresh_signal.direction != direction:
        return False
    return confirmed_regime != _OPPOSING_TREND[direction]


def no_thesis_exit(*, direction, entry_regime, confirmed_regime, fresh_signal) -> bool:
    """H1-NONE (control): no setup re-trigger test at all; the remaining-edge
    check, SL/TP, max hold, giveback and breakeven still apply unchanged."""
    return True


HOLDING_THESES = {
    "H1-DIR": directional_invalidation,
    "H1-NONE": no_thesis_exit,
}
