"""Tests for the order execution state machine (directive section 29)."""

from __future__ import annotations

import pytest

from adaptive_scalper.execution.state_machine import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    InvalidTransitionError,
    OrderState,
    apply_transition,
    is_terminal,
    validate_transition,
)


def test_broker_ack_is_not_a_fill():
    # SUBMITTED -> ACCEPTED is legal; SUBMITTED -> FILLED directly is not
    # (an order must pass through an intermediate "alive" state first).
    assert validate_transition(OrderState.SUBMITTED, OrderState.ACCEPTED) is True
    assert validate_transition(OrderState.SUBMITTED, OrderState.FILLED) is False


def test_proposed_can_only_advance_to_submitted():
    assert ALLOWED_TRANSITIONS[OrderState.PROPOSED] == frozenset({OrderState.SUBMITTED})


@pytest.mark.parametrize("state", [
    OrderState.SUBMITTED, OrderState.ACCEPTED, OrderState.PENDING, OrderState.RESTING, OrderState.PARTIAL,
])
def test_every_non_terminal_active_state_can_reach_unknown(state):
    assert OrderState.UNKNOWN in ALLOWED_TRANSITIONS[state]


@pytest.mark.parametrize("terminal", sorted(TERMINAL_STATES, key=lambda s: s.value))
def test_terminal_states_have_no_outgoing_transitions(terminal):
    assert ALLOWED_TRANSITIONS[terminal] == frozenset()
    assert is_terminal(terminal) is True


def test_unknown_can_resolve_to_any_terminal_state():
    for terminal in TERMINAL_STATES:
        assert validate_transition(OrderState.UNKNOWN, terminal) is True


def test_unknown_can_resolve_to_partial_or_pending_reconciliation():
    assert validate_transition(OrderState.UNKNOWN, OrderState.PARTIAL) is True
    assert validate_transition(OrderState.UNKNOWN, OrderState.PENDING_RECONCILIATION) is True


def test_apply_transition_returns_new_state_when_legal():
    assert apply_transition(OrderState.PROPOSED, OrderState.SUBMITTED) == OrderState.SUBMITTED


def test_apply_transition_raises_on_illegal_jump():
    with pytest.raises(InvalidTransitionError):
        apply_transition(OrderState.PROPOSED, OrderState.FILLED)


def test_apply_transition_raises_from_a_terminal_state():
    with pytest.raises(InvalidTransitionError):
        apply_transition(OrderState.FILLED, OrderState.CANCELLED)


def test_every_order_state_has_an_entry_in_allowed_transitions():
    # No state can silently fall through to an unspecified (and thus
    # accidentally permissive) default.
    for state in OrderState:
        assert state in ALLOWED_TRANSITIONS
