"""Order execution state machine (directive section 29).

Pure, no I/O: `validate_transition()`/`apply_transition()` are the single
source of truth for which state changes are legal. Broker acknowledgement
is NOT a fill — `ACCEPTED` and `FILLED` are distinct states, and nothing
in this module lets a caller skip from `SUBMITTED` straight to `FILLED`
without passing through an intermediate state (or landing in `UNKNOWN`,
which is always a legal destination from any non-terminal state — an
order whose outcome genuinely can't be determined must be representable
at every point in its life, not just after submission).
"""

from __future__ import annotations

from enum import Enum


class OrderState(str, Enum):
    PROPOSED = "PROPOSED"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PENDING = "PENDING"
    RESTING = "RESTING"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"
    PENDING_RECONCILIATION = "PENDING_RECONCILIATION"


TERMINAL_STATES = frozenset({
    OrderState.FILLED, OrderState.REJECTED, OrderState.CANCELLED, OrderState.EXPIRED,
})


class InvalidTransitionError(ValueError):
    """Raised when a caller attempts a state change `ALLOWED_TRANSITIONS`
    does not permit. This must never be caught and routed around — an
    invalid transition means the caller's own understanding of the
    order's lifecycle is wrong, which is exactly the class of bug this
    state machine exists to catch before it corrupts persisted state."""


# Every non-terminal state can always reach UNKNOWN (an order whose
# outcome cannot currently be determined can occur at any point in its
# life) and UNKNOWN/PENDING_RECONCILIATION can resolve to any terminal
# state OR PARTIAL once reconciliation determines the truth.
ALLOWED_TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    OrderState.PROPOSED: frozenset({OrderState.SUBMITTED}),
    OrderState.SUBMITTED: frozenset({OrderState.ACCEPTED, OrderState.REJECTED, OrderState.UNKNOWN}),
    OrderState.ACCEPTED: frozenset({
        OrderState.PENDING, OrderState.RESTING, OrderState.FILLED, OrderState.PARTIAL, OrderState.UNKNOWN,
    }),
    OrderState.PENDING: frozenset({
        OrderState.RESTING, OrderState.FILLED, OrderState.PARTIAL,
        OrderState.CANCELLED, OrderState.EXPIRED, OrderState.UNKNOWN,
    }),
    OrderState.RESTING: frozenset({
        OrderState.FILLED, OrderState.PARTIAL, OrderState.CANCELLED, OrderState.EXPIRED, OrderState.UNKNOWN,
    }),
    OrderState.PARTIAL: frozenset({OrderState.FILLED, OrderState.CANCELLED, OrderState.UNKNOWN}),
    OrderState.UNKNOWN: frozenset({
        OrderState.PENDING_RECONCILIATION, OrderState.FILLED, OrderState.REJECTED,
        OrderState.CANCELLED, OrderState.EXPIRED, OrderState.PARTIAL,
    }),
    OrderState.PENDING_RECONCILIATION: frozenset({
        OrderState.FILLED, OrderState.REJECTED, OrderState.CANCELLED,
        OrderState.EXPIRED, OrderState.PARTIAL, OrderState.UNKNOWN,
    }),
    OrderState.FILLED: frozenset(),
    OrderState.REJECTED: frozenset(),
    OrderState.CANCELLED: frozenset(),
    OrderState.EXPIRED: frozenset(),
}


def validate_transition(from_state: OrderState, to_state: OrderState) -> bool:
    return to_state in ALLOWED_TRANSITIONS.get(from_state, frozenset())


def apply_transition(from_state: OrderState, to_state: OrderState) -> OrderState:
    """Returns `to_state` if the transition is legal; raises
    `InvalidTransitionError` otherwise. A thin wrapper over
    `validate_transition()` that fails loudly instead of leaving the
    caller to remember to check the boolean."""
    if not validate_transition(from_state, to_state):
        raise InvalidTransitionError(f"{from_state.value} -> {to_state.value} is not a legal transition")
    return to_state


def is_terminal(state: OrderState) -> bool:
    return state in TERMINAL_STATES
