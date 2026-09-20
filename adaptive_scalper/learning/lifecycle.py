"""Model lifecycle state machine (directive: BASELINE → CHALLENGER →
CURRENT → PREVIOUS_STABLE → REJECTED → DEGRADED → ROLLED_BACK →
INSUFFICIENT_DATA).

Pure, no I/O — mirrors `execution.state_machine`'s design exactly
(single source of truth for legal transitions, `apply_transition()`
raises rather than letting an illegal jump reach persisted state).

- A freshly registered model starts `BASELINE` (the first model for a
  key) or `INSUFFICIENT_DATA` (not enough training samples yet).
- `INSUFFICIENT_DATA` -> `CHALLENGER` once enough data exists to attempt
  promotion.
- `BASELINE`/`CHALLENGER` -> `CURRENT` (promoted) or `REJECTED` (failed
  the promotion gate) or back to `INSUFFICIENT_DATA` (data turned out
  insufficient after all).
- `CURRENT` -> `PREVIOUS_STABLE` (superseded by a newly promoted
  `CURRENT`) or `DEGRADED` (live drift monitoring detected degradation —
  directive: "Drift lowers model influence. Never raises risk").
- `DEGRADED` -> `ROLLED_BACK` (operator/system reverts to a prior stable
  model).
- `ROLLED_BACK`/`PREVIOUS_STABLE` -> `CURRENT` (re-promoted).
- `REJECTED` is terminal for that specific model version — a rejected
  challenger never gets a second life; a new attempt registers a NEW
  version instead.
"""

from __future__ import annotations

from enum import Enum


class ModelLifecycleState(str, Enum):
    BASELINE = "BASELINE"
    CHALLENGER = "CHALLENGER"
    CURRENT = "CURRENT"
    PREVIOUS_STABLE = "PREVIOUS_STABLE"
    REJECTED = "REJECTED"
    DEGRADED = "DEGRADED"
    ROLLED_BACK = "ROLLED_BACK"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class InvalidLifecycleTransitionError(ValueError):
    """Raised when a caller attempts a state change `ALLOWED_TRANSITIONS`
    does not permit. Never caught and routed around."""


ALLOWED_TRANSITIONS: dict[ModelLifecycleState, frozenset[ModelLifecycleState]] = {
    ModelLifecycleState.INSUFFICIENT_DATA: frozenset({ModelLifecycleState.CHALLENGER}),
    ModelLifecycleState.BASELINE: frozenset({
        ModelLifecycleState.CHALLENGER, ModelLifecycleState.CURRENT,
        ModelLifecycleState.REJECTED, ModelLifecycleState.INSUFFICIENT_DATA,
    }),
    ModelLifecycleState.CHALLENGER: frozenset({
        ModelLifecycleState.CURRENT, ModelLifecycleState.REJECTED, ModelLifecycleState.INSUFFICIENT_DATA,
    }),
    ModelLifecycleState.CURRENT: frozenset({
        ModelLifecycleState.PREVIOUS_STABLE, ModelLifecycleState.DEGRADED,
    }),
    ModelLifecycleState.PREVIOUS_STABLE: frozenset({ModelLifecycleState.CURRENT}),
    ModelLifecycleState.DEGRADED: frozenset({ModelLifecycleState.ROLLED_BACK}),
    ModelLifecycleState.ROLLED_BACK: frozenset({ModelLifecycleState.PREVIOUS_STABLE, ModelLifecycleState.CURRENT}),
    ModelLifecycleState.REJECTED: frozenset(),
}

TERMINAL_STATES = frozenset({ModelLifecycleState.REJECTED})


def validate_transition(from_state: ModelLifecycleState, to_state: ModelLifecycleState) -> bool:
    return to_state in ALLOWED_TRANSITIONS.get(from_state, frozenset())


def apply_transition(from_state: ModelLifecycleState, to_state: ModelLifecycleState) -> ModelLifecycleState:
    if not validate_transition(from_state, to_state):
        raise InvalidLifecycleTransitionError(f"{from_state.value} -> {to_state.value} is not a legal transition")
    return to_state


def is_terminal(state: ModelLifecycleState) -> bool:
    return state in TERMINAL_STATES
